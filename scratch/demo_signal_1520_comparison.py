import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.strategy.params import BacktestConfig, ExecutionParams
from app.execution.engine import run_backtest
from app.data.loader import get_available_symbols

def main():
    symbols = get_available_symbols()[:10]
    print(f"Testing with {len(symbols)} symbols: {symbols}")

    # Run 1: 15:20 Signal Time
    cfg_1520 = BacktestConfig(
        symbols=symbols,
        start_date="2023-01-01",
        end_date="2023-12-31",
        signal_time="15:20",
        execution=ExecutionParams(exit_time="09:15", entry_time="15:20")
    )
    res_1520 = run_backtest(cfg_1520)

    # Run 2: 15:25 Signal Time
    cfg_1525 = BacktestConfig(
        symbols=symbols,
        start_date="2023-01-01",
        end_date="2023-12-31",
        signal_time="15:25",
        execution=ExecutionParams(exit_time="09:15", entry_time="15:25")
    )
    res_1525 = run_backtest(cfg_1525)

    m20 = res_1520["metrics"]
    m25 = res_1525["metrics"]

    print("\n" + "=" * 60)
    print("BACKTEST COMPARISON: 15:20 (Groww Window) vs 15:25")
    print("=" * 60)
    print(f"Metric                     | 15:20 Check & Entry | 15:25 Check & Entry")
    print("-" * 60)
    print(f"Total Trades               | {m20['n_trades']:19d} | {m25['n_trades']:19d}")
    print(f"Total Signals Generated    | {m20['total_signals_generated']:19d} | {m25['total_signals_generated']:19d}")
    print(f"Total Return %             | {m20['total_return_pct']:18.2f}% | {m25['total_return_pct']:18.2f}%")
    print(f"Total Net P&L (INR)        | {m20['total_net_pnl']:19.2f} | {m25['total_net_pnl']:19.2f}")
    print(f"Win Rate %                 | {m20['win_rate_pct']:18.2f}% | {m25['win_rate_pct']:18.2f}%")
    print(f"Max Drawdown %             | {m20['max_drawdown_pct']:18.2f}% | {m25['max_drawdown_pct']:18.2f}%")
    print("=" * 60)

    if res_1520["trades"]:
        sample_t = res_1520["trades"][0]
        print("\nSample 15:20 Trade:")
        print(f"Symbol: {sample_t['symbol']}, Entry Date: {sample_t['entry_date']}, Entry Time: {sample_t['entry_time']}, Entry Price: {sample_t['entry_price']}, Exit Price: {sample_t['exit_price']}")
        assert sample_t['entry_time'] == '15:20'
        print("Verification: Trade entry_time correctly recorded as 15:20!")

if __name__ == "__main__":
    main()
