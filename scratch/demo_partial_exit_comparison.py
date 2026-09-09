import json
import os
import sys

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.data.loader import get_available_symbols
from app.strategy.params import BacktestConfig, PartialExitParams, ExecutionParams
from app.execution.engine import run_backtest

def main():
    syms = get_available_symbols()[:25]
    print(f"Running comparison on {len(syms)} symbols...")

    scenarios = {
        "09:15 Full (Baseline)": BacktestConfig(
            symbols=syms,
            start_date="2024-01-01",
            end_date="2024-06-30",
            execution=ExecutionParams(exit_time="09:15"),
            partial_exit=PartialExitParams(partial_exit_enabled=False),
        ),
        "09:20 Full Exit": BacktestConfig(
            symbols=syms,
            start_date="2024-01-01",
            end_date="2024-06-30",
            execution=ExecutionParams(exit_time="09:20"),
            partial_exit=PartialExitParams(partial_exit_enabled=False),
        ),
        "09:30 Full Exit": BacktestConfig(
            symbols=syms,
            start_date="2024-01-01",
            end_date="2024-06-30",
            execution=ExecutionParams(exit_time="09:30"),
            partial_exit=PartialExitParams(partial_exit_enabled=False),
        ),
        "50% @ 09:15 + 50% @ 09:20": BacktestConfig(
            symbols=syms,
            start_date="2024-01-01",
            end_date="2024-06-30",
            partial_exit=PartialExitParams(
                partial_exit_enabled=True,
                partial_exit_first_pct=50.0,
                partial_exit_first_time="09:15",
                partial_exit_second_pct=50.0,
                partial_exit_second_time="09:20",
            ),
        ),
        "50% @ 09:15 + 50% @ 09:30": BacktestConfig(
            symbols=syms,
            start_date="2024-01-01",
            end_date="2024-06-30",
            partial_exit=PartialExitParams(
                partial_exit_enabled=True,
                partial_exit_first_pct=50.0,
                partial_exit_first_time="09:15",
                partial_exit_second_pct=50.0,
                partial_exit_second_time="09:30",
            ),
        ),
        "50% @ 09:15 + 50% @ 15:00": BacktestConfig(
            symbols=syms,
            start_date="2024-01-01",
            end_date="2024-06-30",
            partial_exit=PartialExitParams(
                partial_exit_enabled=True,
                partial_exit_first_pct=50.0,
                partial_exit_first_time="09:15",
                partial_exit_second_pct=50.0,
                partial_exit_second_time="15:00",
            ),
        ),
    }

    results = {}
    for name, cfg in scenarios.items():
        results[name] = run_backtest(cfg)

    print("\n" + "=" * 90)
    print("BACKTEST EXIT STRATEGY COMPARISON")
    print("=" * 90)
    header = f"{'Scenario':<28} | {'Trades':<7} | {'Win Rate':<9} | {'Net P&L (Rs)':<12} | {'Return %':<9} | {'Max DD %':<9}"
    print(header)
    print("-" * 90)
    for name, res in results.items():
        m = res["metrics"]
        print(
            f"{name:<28} | "
            f"{m['n_trades']:<7} | "
            f"{m['win_rate_pct']:.1f}%{'':<3} | "
            f"Rs.{m['total_net_pnl']:<9.2f} | "
            f"{m['total_return_pct']:<8.2f}% | "
            f"{m['max_drawdown_pct']:<8.2f}%"
        )

    # Show a detailed sample trade from the 09:15 + 09:30 scenario
    res_930 = results["50% @ 09:15 + 50% @ 09:30"]
    sample_trade = None
    for t in res_930["trades"]:
        if len(t.get("exits", [])) == 2:
            sample_trade = t
            break

    if sample_trade:
        print("\n" + "=" * 70)
        print("SAMPLE PARTIAL EXIT TRADE (09:15 + 09:30)")
        print("=" * 70)
        print(f"Symbol: {sample_trade['symbol']}")
        print(f"Entry Date: {sample_trade['entry_date']} @ 15:25 IST")
        print(f"Entry Price: Rs. {sample_trade['entry_price']}")
        print(f"Total Qty: {sample_trade['qty']} shares")
        for idx, leg in enumerate(sample_trade["exits"], start=1):
            print(f"  Leg {idx} ({leg['exit_time']} IST on {leg['exit_date']}): {leg['qty']} shares @ Rs. {leg['exit_price']} -> Net P&L: Rs. {leg['net_pnl']}")
        print(f"Aggregated Trade Net P&L: Rs. {sample_trade['net_pnl']} ({sample_trade['return_pct']}%)")

if __name__ == "__main__":
    main()
