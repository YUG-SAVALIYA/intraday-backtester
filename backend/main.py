"""
FastAPI backend for the Overnight Breakout Backtester.
Serves: backtest, optimize, signal debugger, data catalog endpoints.
"""
import asyncio
import logging
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Optional

import json
import numpy as np
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


class _NumpyEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy scalars (bool, int64, float64, etc.)."""
    def default(self, obj):
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def json_response(data) -> Response:
    """Return a Response with numpy-safe JSON encoding."""
    content = json.dumps(data, cls=_NumpyEncoder, allow_nan=False)
    return Response(content=content, media_type="application/json")

# Add parent dir to path so 'app' imports work
sys.path.insert(0, os.path.dirname(__file__))

from app.data.loader import get_available_symbols, load_symbol, get_symbol_date_range, clear_cache
from app.execution.engine import run_backtest
from app.optimizer.sweep import run_optimization, build_param_grid
from app.strategy.params import BacktestConfig, StrategyParams
from app.strategy.signals import compute_signals_for_symbol

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("main")

app = FastAPI(title="Overnight Breakout Backtester", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store for long-running tasks
_JOBS: dict[str, dict] = {}
_EXECUTOR = ThreadPoolExecutor(max_workers=4)

# ──────────────────────────────────────────────────────────────────────────────
# Pydantic request models
# ──────────────────────────────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    config: dict = {}  # Serialized BacktestConfig dict


class OptimizeRequest(BaseModel):
    base_config: dict = {}
    param_ranges: list[dict] = []
    sort_by: str = "total_net_pnl"
    min_trades: int = 10
    insample_end_date: Optional[str] = None
    max_workers: int = 2


class SignalDebugRequest(BaseModel):
    symbol: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    strategy_params: dict = {}


# ──────────────────────────────────────────────────────────────────────────────
# Helper: async job runner
# ──────────────────────────────────────────────────────────────────────────────

def _submit_job(job_id: str, fn, *args, **kwargs):
    """Run fn in background thread, store result in _JOBS."""
    _JOBS[job_id] = {
        "status": "running",
        "started_at": datetime.utcnow().isoformat(),
        "progress": "Initializing backtest...",
    }

    def _progress(msg: str):
        if job_id in _JOBS:
            _JOBS[job_id]["progress"] = msg

    def _wrapper():
        try:
            import inspect
            sig = inspect.signature(fn)
            if "progress_callback" in sig.parameters:
                kwargs["progress_callback"] = _progress
            result = fn(*args, **kwargs)
            _JOBS[job_id] = {
                "status": "done",
                "result": result,
                "progress": "Completed",
                "finished_at": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.exception(f"Job {job_id} failed: {e}")
            _JOBS[job_id] = {"status": "error", "error": str(e), "progress": f"Failed: {e}"}

    _EXECUTOR.submit(_wrapper)


# ──────────────────────────────────────────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


# ── Data endpoints ──────────────────────────────────────────────────────────

@app.get("/api/data/symbols")
def list_symbols():
    """Return all available symbols and their date ranges."""
    symbols = get_available_symbols()
    return {"symbols": symbols, "count": len(symbols)}


@app.get("/api/data/symbol/{symbol}")
def symbol_info(symbol: str):
    """Return metadata and first/last dates for a symbol."""
    df = load_symbol(symbol)
    if df is None:
        raise HTTPException(404, f"Symbol {symbol} not found")
    start, end = df["date"].iloc[0], df["date"].iloc[-1]
    return {
        "symbol": symbol,
        "rows": len(df),
        "start_date": str(start),
        "end_date": str(end),
    }


@app.delete("/api/data/cache")
def clear_data_cache():
    clear_cache()
    return {"message": "Cache cleared"}


@app.get("/api/universe/{name}")
def get_universe(name: str):
    """Fetch symbols for a specific universe (e.g. nifty50, nifty100)."""
    name = name.lower()
    
    # If "all", return all available parquet symbols
    if name == "all":
        return {"universe": "all", "symbols": get_available_symbols()}
        
    url_map = {
        "nifty50": "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
        "nifty100": "https://archives.nseindia.com/content/indices/ind_nifty100list.csv",
        "nifty200": "https://archives.nseindia.com/content/indices/ind_nifty200list.csv",
        "nifty500": "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    }
    
    if name not in url_map:
        raise HTTPException(400, f"Unknown universe {name}")
        
    try:
        import pandas as pd
        df = pd.read_csv(url_map[name])
        symbols = df["Symbol"].tolist()
        
        # Only return symbols that we actually have parquet data for
        available = set(get_available_symbols())
        valid_symbols = [s for s in symbols if s in available]
        
        return {"universe": name, "symbols": valid_symbols}
    except Exception as e:
        logger.error(f"Error fetching universe {name}: {e}")
        raise HTTPException(500, f"Error fetching universe: {e}")


# ── Default config endpoint ─────────────────────────────────────────────────

@app.get("/api/config/defaults")
def get_defaults():
    """Return default BacktestConfig as JSON for the UI to pre-populate."""
    return BacktestConfig().to_dict()


# ── Backtest endpoints ───────────────────────────────────────────────────────

@app.post("/api/backtest/run")
def start_backtest(req: BacktestRequest):
    """Submit a backtest job. Returns job_id to poll."""
    try:
        config = BacktestConfig.from_dict(req.config)
    except Exception as e:
        raise HTTPException(400, f"Invalid config: {e}")

    job_id = str(uuid.uuid4())
    _submit_job(job_id, run_backtest, config)
    return {"job_id": job_id, "status": "running"}


@app.post("/api/backtest/run/sync")
def run_backtest_sync(req: BacktestRequest):
    """Run backtest synchronously and return results (for small datasets)."""
    try:
        config = BacktestConfig.from_dict(req.config)
    except Exception as e:
        raise HTTPException(400, f"Invalid config: {e}")
    try:
        result = run_backtest(config)
        return json_response(result)
    except Exception as e:
        logger.exception("Backtest error")
        raise HTTPException(500, str(e))


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    """Poll job status and result."""
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.get("status") == "running":
        return {
            "status": "running",
            "progress": job.get("progress", "Running backtest..."),
            "started_at": job.get("started_at"),
        }
    return json_response(job)


# ── Past Runs Endpoints ───────────────────────────────────────────────────────

OUTPUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "output"))

@app.get("/api/runs")
def list_past_runs():
    """List all saved backtest run files from the output directory with summary stats."""
    if not os.path.isdir(OUTPUT_DIR):
        return {"runs": [], "count": 0}

    files = [f for f in os.listdir(OUTPUT_DIR) if f.endswith(".json") and f != "latest_run.json"]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(OUTPUT_DIR, f)), reverse=True)

    runs = []
    for f in files:
        filepath = os.path.join(OUTPUT_DIR, f)
        try:
            stat = os.stat(filepath)
            size_kb = round(stat.st_size / 1024, 1)
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

            summary = {
                "filename": f,
                "timestamp": mtime,
                "size_kb": size_kb,
                "n_trades": 0,
                "total_return_pct": 0.0,
                "total_net_pnl": 0.0,
                "win_rate_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "symbols_count": 0,
                "start_date": "",
                "end_date": "",
            }

            with open(filepath, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                m = data.get("metrics", {})
                cfg = data.get("config", {})
                summary["n_trades"] = m.get("n_trades", 0)
                summary["total_return_pct"] = m.get("total_return_pct", 0.0)
                summary["total_net_pnl"] = m.get("total_net_pnl", 0.0)
                summary["win_rate_pct"] = m.get("win_rate_pct", 0.0)
                summary["max_drawdown_pct"] = m.get("max_drawdown_pct", 0.0)
                summary["start_date"] = cfg.get("start_date", "")
                summary["end_date"] = cfg.get("end_date", "")
                summary["symbols_count"] = len(cfg.get("symbols", []))
            runs.append(summary)
        except Exception as e:
            logger.warning(f"Error reading metadata for {f}: {e}")

    return {"runs": runs, "count": len(runs)}


@app.get("/api/runs/{filename}")
def get_past_run(filename: str):
    """Retrieve full content of a saved backtest run."""
    clean_filename = os.path.basename(filename)
    filepath = os.path.join(OUTPUT_DIR, clean_filename)
    if not os.path.isfile(filepath):
        raise HTTPException(404, f"Run file {clean_filename} not found")

    try:
        with open(filepath, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return json_response(data)
    except Exception as e:
        logger.exception(f"Error reading {clean_filename}: {e}")
        raise HTTPException(500, f"Error reading run file: {e}")


@app.delete("/api/runs/{filename}")
def delete_past_run(filename: str):
    """Delete a saved run file."""
    clean_filename = os.path.basename(filename)
    filepath = os.path.join(OUTPUT_DIR, clean_filename)
    if not os.path.isfile(filepath):
        raise HTTPException(404, f"Run file {clean_filename} not found")
    try:
        os.remove(filepath)
        return {"status": "ok", "deleted": clean_filename}
    except Exception as e:
        raise HTTPException(500, f"Failed to delete {clean_filename}: {e}")



# ── Optimization endpoints ───────────────────────────────────────────────────

@app.post("/api/optimize/run")
def start_optimization(req: OptimizeRequest):
    """Submit an optimization job. Returns job_id."""
    try:
        base_config = BacktestConfig.from_dict(req.base_config)
    except Exception as e:
        raise HTTPException(400, f"Invalid base_config: {e}")

    # Validate param_ranges
    if not req.param_ranges:
        raise HTTPException(400, "param_ranges cannot be empty")

    # Count combinations
    grid = build_param_grid(req.param_ranges)
    if len(grid) > 50_000:
        raise HTTPException(400, f"Too many combinations ({len(grid)}). Reduce ranges or increase step.")

    job_id = str(uuid.uuid4())
    _submit_job(
        job_id,
        run_optimization,
        base_config,
        req.param_ranges,
        req.sort_by,
        req.min_trades,
        req.insample_end_date,
        req.max_workers,
    )
    return {"job_id": job_id, "total_combos": len(grid), "status": "running"}


@app.post("/api/optimize/preview")
def preview_param_grid(req: OptimizeRequest):
    """Preview the number of combinations without running."""
    grid = build_param_grid(req.param_ranges)
    return {"total_combos": len(grid), "param_names": [r["param"] for r in req.param_ranges]}


# ── Signal debugger endpoint ─────────────────────────────────────────────────

@app.post("/api/signals/debug")
def debug_signals(req: SignalDebugRequest):
    """
    Return per-day signal debug info for a single symbol.
    Includes all raw values and pass/fail for every condition.
    """
    df = load_symbol(req.symbol)
    if df is None:
        raise HTTPException(404, f"Symbol {req.symbol} not found")

    # Apply date filter
    if req.start_date:
        from datetime import date
        start = date.fromisoformat(req.start_date)
        df = df[df["date"] >= start]
    if req.end_date:
        from datetime import date
        end = date.fromisoformat(req.end_date)
        df = df[df["date"] <= end]

    if df.empty:
        return {"symbol": req.symbol, "rows": [], "count": 0}

    # Parse strategy params
    try:
        params = StrategyParams(**req.strategy_params) if req.strategy_params else StrategyParams()
    except Exception as e:
        raise HTTPException(400, f"Invalid strategy_params: {e}")

    rows = compute_signals_for_symbol(df.reset_index(drop=True), params)
    return json_response({
        "symbol": req.symbol,
        "count": len(rows),
        "rows": rows,
    })


@app.get("/api/signals/debug/{symbol}")
def debug_signals_get(
    symbol: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """GET version of signal debugger (no custom params)."""
    return debug_signals(SignalDebugRequest(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
    ))


# ── Tests endpoint ───────────────────────────────────────────────────────────

@app.post("/api/tests/run")
def run_tests():
    """Run the pytest test suite and return results."""
    import subprocess
    result = subprocess.run(
        ["python", "-m", "pytest", "app/tests/", "-v", "--tb=short", "--no-header"],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(__file__),
    )
    return {
        "returncode": result.returncode,
        "passed": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


# ── Static files (frontend) ──────────────────────────────────────────────────
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
