"""
FAST parallel optimizer — uses ThreadPoolExecutor so all threads share the
in-process data cache (_CACHE dict in loader.py).

Strategy:
  1. Warm up cache with a single backtest → all symbol data loaded into RAM.
  2. Fire all parameter combos simultaneously across CPU threads.
  3. Each subsequent run is pure CPU simulation — no disk I/O.

Target: max_drawdown_pct < 5% with best possible return.

Run from d:/New Intraday/backend:
    python optimizer_fast.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import itertools, json, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from app.strategy.params import BacktestConfig, StrategyParams, SizingParams, ExecutionParams, FeeParams
from app.execution.engine import run_backtest

# ─────────────────────────────────────────────────────────────────────────────
# Date range — KEEP SAME AS YOUR LAST RUN
# ─────────────────────────────────────────────────────────────────────────────
START_DATE = "2021-01-01"
END_DATE   = "2024-12-31"

MAX_DD_THRESHOLD = 5.0   # Must be below this
MIN_TRADES       = 5     # Skip near-empty runs

# ─────────────────────────────────────────────────────────────────────────────
# FOCUSED grid — 72 combos (chosen for highest DD impact per combo)
# Key levers for DD reduction:
#   allocation_value × max_positions × avg_loss_rate ≈ max drawdown
# Signal quality params tightened to reduce losing trades
# ─────────────────────────────────────────────────────────────────────────────
PARAM_GRID = {
    # The two biggest drawdown levers
    "allocation_value":  [5.0, 8.0, 10.0, 15.0, 20.0],
    "max_positions":     [1, 2, 3, 5],

    # Volume spike quality — higher = fewer but cleaner trades
    "volume_multiplier": [2.0, 2.5, 3.0, 4.0],

    # Signal quality — tighter = fewer losses
    "min_range_pct":     [10.0, 15.0],
    "min_close_loc_pct": [90.0, 95.0],

    # Keep body fixed (less impactful)
    "min_body_pct":      [5.0],
}
# Total: 5×4×4×2×2×1 = 320 combos


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
            "cagr_pct":         round(m["cagr_pct"], 4),
            "max_drawdown_pct": round(m["max_drawdown_pct"], 4),
            "profit_factor":    round(m["profit_factor"], 4),
            "sharpe_ratio":     round(m["sharpe_ratio"], 4),
        }
    except Exception as e:
        return {"_error": str(e), **p}


def main():
    keys   = list(PARAM_GRID.keys())
    combos = [dict(zip(keys, v)) for v in itertools.product(*PARAM_GRID.values())]
    total  = len(combos)

    print(f"\n{'='*72}")
    print(f"  FAST Parallel Parameter Optimizer")
    print(f"  Combinations  : {total}")
    print(f"  Date range    : {START_DATE} to {END_DATE}")
    print(f"  DD target     : below {MAX_DD_THRESHOLD}%")
    print(f"{'='*72}")

    # ── Step 1: Warm up the data cache (single-threaded first run) ────────
    print("\n[WARM-UP] Loading all symbol data into RAM cache...")
    t0 = time.time()
    _run(combos[0])  # This loads all parquet files into _CACHE
    print(f"  Cache warm in {time.time()-t0:.1f}s — all subsequent runs are CPU-only.\n")

    # ── Step 2: Run all remaining combos in parallel ───────────────────────
    # Use min(16, cpu_count) threads — NumPy releases GIL so threads run truly parallel
    import multiprocessing
    n_workers = min(16, multiprocessing.cpu_count() * 2)
    print(f"[RUN] {total} combos across {n_workers} threads ...\n")

    results  = []
    done     = 0
    t_start  = time.time()

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_run, p): p for p in combos}
        for fut in as_completed(futures):
            done += 1
            r = fut.result()
            if r and "_error" not in r and r.get("n_trades", 0) >= MIN_TRADES:
                results.append(r)
                dd  = r["max_drawdown_pct"]
                ret = r["total_return_pct"]
                status = "PASS" if dd < MAX_DD_THRESHOLD else "    "
                if dd < MAX_DD_THRESHOLD or done % 20 == 0:
                    elapsed = time.time() - t_start
                    eta = (elapsed / done) * (total - done)
                    print(f"  [{done:3d}/{total}] {status}  "
                          f"alloc={r['allocation_value']:4.0f}% pos={r['max_positions']} "
                          f"vol x{r['volume_multiplier']:.1f} "
                          f"range>={r['min_range_pct']:.0f}% loc>={r['min_close_loc_pct']:.0f}% | "
                          f"DD={dd:5.2f}%  Ret={ret:7.2f}%  T={r['n_trades']}  "
                          f"ETA={eta:.0f}s")

    elapsed = time.time() - t_start

    # ── Step 3: Report ─────────────────────────────────────────────────────
    passed = sorted([r for r in results if r["max_drawdown_pct"] < MAX_DD_THRESHOLD],
                    key=lambda x: x["total_return_pct"], reverse=True)
    failed = sorted([r for r in results if r["max_drawdown_pct"] >= MAX_DD_THRESHOLD],
                    key=lambda x: x["max_drawdown_pct"])

    print(f"\n{'='*72}")
    print(f"  Completed {total} runs in {elapsed:.1f}s")
    print(f"  PASSED (DD < {MAX_DD_THRESHOLD}%): {len(passed)}   Failed: {len(failed)}")
    print(f"{'='*72}\n")

    def fmt(r, rank=None):
        prefix = f"  #{rank:<3}" if rank else "  "
        print(f"{prefix} alloc={r['allocation_value']}%  pos={r['max_positions']}  "
              f"vol×{r['volume_multiplier']}  range>={r['min_range_pct']}%  "
              f"loc>={r['min_close_loc_pct']}%  body>{r['min_body_pct']}%")
        print(f"       Trades={r['n_trades']}  WR={r['win_rate_pct']:.1f}%  "
              f"Return={r['total_return_pct']:.2f}%  CAGR={r['cagr_pct']:.2f}%  "
              f"DD={r['max_drawdown_pct']:.2f}%  PF={r['profit_factor']:.2f}  "
              f"Sharpe={r['sharpe_ratio']:.2f}\n")

    if not passed:
        print("  No combinations achieved DD < 5%. Top 10 closest:\n")
        for r in failed[:10]:
            fmt(r)
    else:
        print(f"  TOP {min(20, len(passed))} RESULTS (DD < {MAX_DD_THRESHOLD}%, best return first):\n")
        for i, r in enumerate(passed[:20], 1):
            fmt(r, rank=i)

        best = passed[0]
        print("="*72)
        print("  BEST RECOMMENDED PARAMETERS")
        print("="*72)
        fmt(best, rank=1)
        print("  Set these in the UI or params.py:")
        print(f"    min_range_pct     = {best['min_range_pct']}")
        print(f"    min_body_pct      = {best['min_body_pct']}")
        print(f"    min_close_loc_pct = {best['min_close_loc_pct']}")
        print(f"    volume_multiplier = {best['volume_multiplier']}")
        print(f"    allocation_value  = {best['allocation_value']}  (% per trade)")
        print(f"    max_positions     = {best['max_positions']}")

    # Save JSON
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opt_results.json")
    with open(out_path, "w") as f:
        json.dump({
            "run_at": datetime.now().isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            "start_date": START_DATE, "end_date": END_DATE,
            "dd_threshold": MAX_DD_THRESHOLD,
            "passed": passed,
            "best": passed[0] if passed else None,
            "closest_failing": failed[:5],
        }, f, indent=2)
    print(f"\n  Full results saved: {out_path}")


if __name__ == "__main__":
    main()
