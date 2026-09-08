"""
Ultra-fast parameter optimizer to find max drawdown < 5% in < 2 mins.
Grid size: 18 combinations.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import itertools, json, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from app.strategy.params import BacktestConfig, StrategyParams, SizingParams, ExecutionParams, FeeParams
from app.execution.engine import run_backtest

START_DATE = "2021-01-01"
END_DATE   = "2024-12-31"
MAX_DD_THRESHOLD = 5.0
MIN_TRADES = 5

PARAM_GRID = {
    # Narrow grid to find <5% DD very quickly
    "allocation_value":  [5.0, 10.0, 15.0],
    "max_positions":     [2, 3, 5],
    "volume_multiplier": [2.5, 3.0],
    "min_range_pct":     [10.0],
    "min_close_loc_pct": [90.0],
    "min_body_pct":      [5.0],
}

def _make_cfg(p: dict) -> BacktestConfig:
    return BacktestConfig(
        strategy=StrategyParams(
            min_range_pct     = p["min_range_pct"],
            min_body_pct      = p["min_body_pct"],
            min_close_loc_pct = p["min_close_loc_pct"],
            volume_lookback   = 20,
            volume_multiplier = p["volume_multiplier"],
        ),
        execution=ExecutionParams(),
        sizing=SizingParams(
            initial_capital  = 100_000.0,
            allocation_type  = "pct",
            allocation_value = p["allocation_value"],
            max_positions    = p["max_positions"],
            max_exposure_pct = 100.0,
            leverage         = 1.0,
            reinvest         = True,
        ),
        fees=FeeParams(),
        start_date=START_DATE,
        end_date=END_DATE,
    )

def _run(p: dict):
    try:
        m = run_backtest(_make_cfg(p))["metrics"]
        return {
            **p,
            "n_trades":         m["n_trades"],
            "win_rate_pct":     round(m["win_rate_pct"], 2),
            "total_return_pct": round(m["total_return_pct"], 4),
            "max_drawdown_pct": round(m["max_drawdown_pct"], 4),
        }
    except Exception as e:
        return {"_error": str(e), **p}

def main():
    keys = list(PARAM_GRID.keys())
    combos = [dict(zip(keys, v)) for v in itertools.product(*PARAM_GRID.values())]
    
    print("[WARM-UP] Loading cache...")
    _run(combos[0])
    print("[WARM-UP] Done. Running grid...")

    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_run, p): p for p in combos}
        for fut in as_completed(futures):
            r = fut.result()
            if r and "_error" not in r:
                results.append(r)
                print(f"Alloc={r['allocation_value']}% Pos={r['max_positions']} VolX={r['volume_multiplier']} => DD={r['max_drawdown_pct']}% Ret={r['total_return_pct']}% Trades={r['n_trades']}")

    passed = sorted([r for r in results if r["max_drawdown_pct"] < MAX_DD_THRESHOLD],
                    key=lambda x: x["total_return_pct"], reverse=True)

    if passed:
        best = passed[0]
        print(f"\nWINNER: {best}")
        out_path = os.path.join(os.path.dirname(__file__), "best_params.json")
        with open(out_path, "w") as f:
            json.dump(best, f)
    else:
        print("\nNO COMBINATION FOUND < 5% DD.")
        failed = sorted(results, key=lambda x: x["max_drawdown_pct"])
        print(f"BEST DD: {failed[0]['max_drawdown_pct']}%")

if __name__ == "__main__":
    main()
