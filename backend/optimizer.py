"""
Parameter optimizer: exhaustive grid-search over StrategyParams + SizingParams.
Goal: find parameter sets where max_drawdown_pct < 5% with max total_return_pct.

Run from d:/New Intraday/backend with:
    python optimizer.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import itertools
import json
from datetime import datetime

from app.strategy.params import BacktestConfig, StrategyParams, SizingParams, ExecutionParams, FeeParams
from app.execution.engine import run_backtest

# ─────────────────────────────────────────────────────────────────────────────
# Backtest date range
# ─────────────────────────────────────────────────────────────────────────────
START_DATE = "2021-01-01"
END_DATE   = "2024-12-31"

# ─────────────────────────────────────────────────────────────────────────────
# Parameter grid (no code or logic changes — only tuning values)
# Tighter signal filters = fewer but higher-quality trades = lower drawdown
# Smaller allocation = less capital at risk per trade = lower drawdown
# ─────────────────────────────────────────────────────────────────────────────
PARAM_GRID = {
    "min_range_pct":     [10.0, 12.0, 15.0],        # Wider candle required
    "min_body_pct":      [5.0, 7.0, 10.0],           # Stronger bullish body required
    "min_close_loc_pct": [90.0, 92.0, 95.0],         # Close must be nearer to high
    "volume_multiplier": [2.0, 2.5, 3.0, 4.0],       # Higher volume spike required
    "allocation_value":  [5.0, 10.0, 15.0, 20.0],    # % of equity per trade
    "max_positions":     [1, 2, 3, 5],               # Max simultaneous open positions
}

# ─────────────────────────────────────────────────────────────────────────────
# Filter criteria
# ─────────────────────────────────────────────────────────────────────────────
MAX_DD_THRESHOLD = 5.0   # max_drawdown_pct must be BELOW this
MIN_TRADES       = 5     # skip trivially sparse runs


def run_one(params: dict):
    cfg = BacktestConfig(
        strategy=StrategyParams(
            min_range_pct     = params["min_range_pct"],
            min_body_pct      = params["min_body_pct"],
            min_close_loc_pct = params["min_close_loc_pct"],
            volume_lookback   = 20,
            volume_multiplier = params["volume_multiplier"],
        ),
        execution=ExecutionParams(),
        sizing=SizingParams(
            initial_capital  = 100_000.0,
            allocation_type  = "pct",
            allocation_value = params["allocation_value"],
            max_positions    = params["max_positions"],
            max_exposure_pct = 100.0,
            leverage         = 1.0,
            reinvest         = True,
        ),
        fees=FeeParams(),
        start_date=START_DATE,
        end_date=END_DATE,
    )
    try:
        result = run_backtest(cfg)
        m = result["metrics"]
        return {
            **params,
            "n_trades":         m["n_trades"],
            "win_rate_pct":     m["win_rate_pct"],
            "total_return_pct": round(m["total_return_pct"], 4),
            "cagr_pct":         round(m["cagr_pct"], 4),
            "max_drawdown_pct": round(m["max_drawdown_pct"], 4),
            "profit_factor":    round(m["profit_factor"], 4),
            "sharpe_ratio":     round(m["sharpe_ratio"], 4),
        }
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def fmt(r):
    print(f"    Range>={r['min_range_pct']}%  Body>{r['min_body_pct']}%  "
          f"Loc>={r['min_close_loc_pct']}%  Vol x{r['volume_multiplier']}  "
          f"Alloc={r['allocation_value']}%  MaxPos={r['max_positions']}")
    print(f"    Trades={r['n_trades']}  WR={r['win_rate_pct']:.1f}%  "
          f"Return={r['total_return_pct']:.2f}%  CAGR={r['cagr_pct']:.2f}%  "
          f"DD={r['max_drawdown_pct']:.2f}%  PF={r['profit_factor']:.2f}  "
          f"Sharpe={r['sharpe_ratio']:.2f}\n")


def main():
    keys   = list(PARAM_GRID.keys())
    values = list(PARAM_GRID.values())
    combos = list(itertools.product(*values))
    total  = len(combos)

    print(f"\n{'='*72}")
    print(f"  Parameter Grid-Search Optimizer")
    print(f"  Combinations : {total}")
    print(f"  Date range   : {START_DATE} to {END_DATE}")
    print(f"  DD target    : below {MAX_DD_THRESHOLD}%")
    print(f"{'='*72}\n")

    passed  = []
    failed  = []
    skipped = 0

    for i, combo in enumerate(combos, 1):
        params = dict(zip(keys, combo))
        print(f"[{i:4d}/{total}] "
              f"range>={params['min_range_pct']:4.0f}% "
              f"body>{params['min_body_pct']:4.0f}% "
              f"loc>={params['min_close_loc_pct']:4.0f}% "
              f"vol x{params['volume_multiplier']:3.1f} "
              f"alloc={params['allocation_value']:4.0f}% "
              f"pos={params['max_positions']} ...",
              end=" ", flush=True)

        r = run_one(params)
        if r is None:
            print("FAILED")
            skipped += 1
            continue

        if r["n_trades"] < MIN_TRADES:
            print(f"SKIP ({r['n_trades']} trades only)")
            skipped += 1
            continue

        dd  = r["max_drawdown_pct"]
        ret = r["total_return_pct"]
        wr  = r["win_rate_pct"]
        print(f"DD={dd:5.2f}%  Ret={ret:8.2f}%  WR={wr:5.1f}%  T={r['n_trades']}", end="")

        if dd < MAX_DD_THRESHOLD:
            passed.append(r)
            print("  PASS")
        else:
            failed.append(r)
            print()

    # Sort passing by total return descending
    passed.sort(key=lambda x: x["total_return_pct"], reverse=True)

    print(f"\n{'='*72}")
    print(f"  DONE: {len(passed)} PASSED / {len(failed)} failed / {skipped} skipped")
    print(f"{'='*72}\n")

    if not passed:
        print("No combinations beat the DD threshold. Top 10 by lowest DD:\n")
        failed.sort(key=lambda x: x["max_drawdown_pct"])
        for r in failed[:10]:
            fmt(r)
        out = {"run_at": datetime.now().isoformat(), "passed": [], "best": None,
               "closest": failed[:10] if failed else []}
    else:
        n = min(20, len(passed))
        print(f"TOP {n} RESULTS (DD < {MAX_DD_THRESHOLD}%, best return first):\n")
        for rank, r in enumerate(passed[:n], 1):
            print(f"  Rank #{rank}")
            fmt(r)

        best = passed[0]
        print("=" * 72)
        print("  RECOMMENDED PARAMETERS  (highest return with DD < 5%)")
        print("=" * 72)
        fmt(best)
        print("  Copy into params.py or the UI:")
        print(f"    min_range_pct     = {best['min_range_pct']}")
        print(f"    min_body_pct      = {best['min_body_pct']}")
        print(f"    min_close_loc_pct = {best['min_close_loc_pct']}")
        print(f"    volume_multiplier = {best['volume_multiplier']}")
        print(f"    allocation_value  = {best['allocation_value']}  (% per trade)")
        print(f"    max_positions     = {best['max_positions']}")

        out = {"run_at": datetime.now().isoformat(),
               "start_date": START_DATE, "end_date": END_DATE,
               "dd_threshold": MAX_DD_THRESHOLD,
               "passed": passed, "best": best}

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "optimization_results.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nFull results saved to: {out_path}")


if __name__ == "__main__":
    main()
