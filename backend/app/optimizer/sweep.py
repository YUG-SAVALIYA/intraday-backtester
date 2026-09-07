"""
Parameter sweep optimizer.

Features:
- Define start/end/step for any numerical parameter
- In-sample / out-of-sample split
- Parallel backtest execution
- Sort/filter results by any metric
- Minimum trade count filter
"""
import itertools
import logging
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from typing import Any

from app.strategy.params import BacktestConfig, StrategyParams, SizingParams, ExecutionParams
from app.execution.engine import run_backtest

logger = logging.getLogger(__name__)


def _arange(start: float, stop: float, step: float) -> list[float]:
    """Like np.arange but returns a list and handles float precision."""
    result = []
    val = start
    while val <= stop + step * 1e-9:
        result.append(round(val, 10))
        val += step
    return result


def build_param_grid(param_ranges: list[dict]) -> list[dict]:
    """
    Build all combinations from a list of parameter range specs.

    Each spec: {"param": "strategy.min_range_pct", "start": 8, "end": 15, "step": 1}
    param path: "strategy.min_range_pct" | "sizing.leverage" | "execution.entry_slippage_pct"
    """
    keys = []
    value_lists = []
    for spec in param_ranges:
        keys.append(spec["param"])
        vals = _arange(spec["start"], spec["end"], spec["step"])
        if not vals:
            vals = [spec["start"]]
        value_lists.append(vals)

    combinations = list(itertools.product(*value_lists))
    result = []
    for combo in combinations:
        result.append(dict(zip(keys, combo)))
    return result


def _apply_overrides(base_config: BacktestConfig, overrides: dict) -> BacktestConfig:
    """Clone base_config and apply parameter overrides."""
    d = base_config.to_dict()
    for path, value in overrides.items():
        parts = path.split(".")
        node = d
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = value
    return BacktestConfig.from_dict(d)


def _run_single(args: tuple) -> dict:
    """Top-level function for multiprocessing (must be picklable)."""
    config_dict, overrides = args
    config = BacktestConfig.from_dict(config_dict)
    config = _apply_overrides(config, overrides)
    try:
        result = run_backtest(config)
        metrics = result["metrics"]
        metrics["params"] = overrides
        return {"success": True, "metrics": metrics, "overrides": overrides}
    except Exception as e:
        return {"success": False, "error": str(e), "overrides": overrides}


def run_optimization(
    base_config: BacktestConfig,
    param_ranges: list[dict],
    sort_by: str = "total_net_pnl",
    min_trades: int = 10,
    insample_end_date: str = None,     # ISO date string; if None, no split
    max_workers: int = 4,
) -> dict:
    """
    Run full parameter sweep.

    Parameters
    ----------
    base_config      : BacktestConfig template
    param_ranges     : list of {"param", "start", "end", "step"} dicts
    sort_by          : metric name to sort results by (descending)
    min_trades       : skip combos with fewer trades
    insample_end_date: split date for in-sample / out-of-sample
    max_workers      : parallel processes

    Returns
    -------
    dict with "results" (sorted list), "total_combos", "valid_combos", "best"
    """
    grid = build_param_grid(param_ranges)
    total_combos = len(grid)
    logger.info(f"Starting optimization: {total_combos} combinations, {max_workers} workers")

    config_dict = base_config.to_dict()

    # If IS/OOS split: run IS period
    if insample_end_date:
        is_config_dict = dict(config_dict)
        is_config_dict["end_date"] = insample_end_date
        oos_config_dict = dict(config_dict)
        oos_config_dict["start_date"] = insample_end_date
    else:
        is_config_dict = config_dict
        oos_config_dict = None

    args_list = [(is_config_dict, overrides) for overrides in grid]

    results = []
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_run_single, arg): arg for arg in args_list}
        completed = 0
        for future in as_completed(futures):
            completed += 1
            res = future.result()
            if res["success"]:
                m = res["metrics"]
                if m["n_trades"] >= min_trades:
                    results.append(m)
            if completed % 50 == 0:
                logger.info(f"Optimization progress: {completed}/{total_combos}")

    # If OOS split: also run OOS for each param set in results
    if oos_config_dict and results:
        logger.info("Running out-of-sample period for top results...")
        oos_results = []
        oos_args = [(oos_config_dict, r["params"]) for r in results]
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_run_single, arg): arg for arg in oos_args}
            for future in as_completed(futures):
                res = future.result()
                if res["success"]:
                    oos_results.append(res["metrics"])

        # Merge IS + OOS into results
        oos_by_params = {str(r["params"]): r for r in oos_results}
        for r in results:
            key = str(r["params"])
            oos = oos_by_params.get(key, {})
            r["oos"] = {
                k: oos.get(k, None)
                for k in ["n_trades", "total_net_pnl", "total_return_pct",
                           "cagr_pct", "win_rate_pct", "max_drawdown_pct",
                           "profit_factor", "sharpe_ratio"]
            }

    # Sort
    reverse = sort_by not in ("max_drawdown_pct",)  # these are "lower is better"
    results.sort(key=lambda x: x.get(sort_by, 0) or 0, reverse=reverse)

    return {
        "total_combos": total_combos,
        "valid_combos": len(results),
        "sort_by": sort_by,
        "min_trades_filter": min_trades,
        "results": results,
        "best": results[0] if results else None,
    }
