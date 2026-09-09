import json
import os
import sys

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.data.loader import get_available_symbols
from app.strategy.params import BacktestConfig, PartialExitParams
from app.execution.engine import run_backtest

def main():
    syms = get_available_symbols()[:20]
    print(f"Running comparison on {len(syms)} symbols...")

    # 1. Baseline (Feature OFF)
    cfg_off = BacktestConfig(
        symbols=syms,
        start_date="2024-01-01",
        end_date="2024-06-30",
        partial_exit=PartialExitParams(partial_exit_enabled=False),
    )
    res_off = run_backtest(cfg_off)

    # 2. Feature ON
    cfg_on = BacktestConfig(
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
    )
    res_on = run_backtest(cfg_on)

    print("\n" + "=" * 70)
    print("BACKTEST COMPARISON: FEATURE OFF vs FEATURE ON")
    print("=" * 70)
    print(f"{'Metric':<30} | {'Feature OFF (Baseline)':<20} | {'Feature ON (Partial Exit)':<20}")
    print("-" * 75)
    m_off = res_off["metrics"]
    m_on = res_on["metrics"]
    keys = ["n_trades", "win_rate_pct", "profit_factor", "total_net_pnl", "total_fees", "final_equity", "cagr_pct", "max_drawdown_pct"]
    for k in keys:
        v_off = m_off.get(k, 0)
        v_on = m_on.get(k, 0)
        print(f"{k:<30} | {v_off:<20} | {v_on:<20}")

    print("\n" + "=" * 70)
    print("EXAMPLE TRADE WITH PARTIAL EXITS (09:15 + 15:00)")
    print("=" * 70)
    # Find a trade with 2 exits
    sample_trade = None
    for t in res_on["trades"]:
        if len(t.get("exits", [])) == 2:
            sample_trade = t
            break

    if sample_trade:
        print(f"Symbol: {sample_trade['symbol']}")
        print(f"Entry Date: {sample_trade['entry_date']} @ 15:25 IST")
        print(f"Entry Price: Rs. {sample_trade['entry_price']}")
        print(f"Original Qty: {sample_trade['qty']} shares")
        print(f"Original Entry Fees: Rs. {sample_trade['entry_fees']}")
        print("\nPartial Exits Breakdown:")
        for idx, leg in enumerate(sample_trade["exits"], start=1):
            print(f"  Leg {idx} ({leg['exit_time']} IST on {leg['exit_date']}):")
            print(f"    - Exit Qty: {leg['qty']} shares")
            print(f"    - Exit Price: Rs. {leg['exit_price']}")
            print(f"    - Gross P&L: Rs. {leg['gross_pnl']}")
            print(f"    - Allocated Entry Fees: Rs. {leg['entry_fees']}")
            print(f"    - Exit Fees: Rs. {leg['exit_fees']}")
            print(f"    - Leg Net P&L: Rs. {leg['net_pnl']}")
        print("\nAggregated Parent Trade:")
        print(f"  - Avg Exit Price: Rs. {sample_trade['exit_price']}")
        print(f"  - Total Gross P&L: Rs. {sample_trade['gross_pnl']}")
        print(f"  - Total Entry Fees: Rs. {sample_trade['entry_fees']}")
        print(f"  - Total Exit Fees: Rs. {sample_trade['exit_fees']}")
        print(f"  - Parent Net P&L: Rs. {sample_trade['net_pnl']}")
        print(f"  - Return %: {sample_trade['return_pct']}%")
    else:
        print("No 2-leg trade found.")

if __name__ == "__main__":
    main()
