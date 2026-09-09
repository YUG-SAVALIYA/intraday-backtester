"""
Backtest execution engine.

Chronological day-by-day loop across all symbols.
Strict no-look-ahead: on day T, we only use data available at 15:25 on day T.
Exit happens at next trading day's open (T+1 open).

Performance: 
- Prebuilds numpy arrays per symbol for fast rolling-volume lookup
- Uses parallel data loading
- Runs the main loop in pure Python (avoids numba complexity with dataclasses)
"""
import json
import logging
import math
import os
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd

from app.data.calendar import is_trading_day, next_trading_day, prev_trading_day, get_trading_days
from app.data.loader import load_symbol, load_symbols_bulk, get_available_symbols
from app.strategy.params import BacktestConfig
from app.strategy.signals import compute_signal, SignalResult
from app.sizing.allocator import compute_position_size
from app.execution.fees import compute_fees, apply_slippage
from app.portfolio.accounting import Portfolio, ClosedTrade, DailySnapshot
from app.metrics.calculator import compute_metrics, compute_drawdown_series

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SymbolData: precomputed arrays per symbol for fast access
# ─────────────────────────────────────────────────────────────────────────────

class SymbolData:
    """Holds numpy arrays for a single symbol, aligned to trading calendar."""
    __slots__ = (
        "symbol", "dates", "opens", "highs", "lows", "closes", "volumes",
        "date_to_idx", "prices_1500"
    )

    def __init__(self, symbol: str, df: pd.DataFrame):
        self.symbol = symbol
        self.dates = np.array(df["date"].tolist(), dtype=object)
        self.opens = df["open"].to_numpy(dtype=np.float64)
        self.highs = df["high"].to_numpy(dtype=np.float64)
        self.lows = df["low"].to_numpy(dtype=np.float64)
        self.closes = df["close"].to_numpy(dtype=np.float64)
        self.volumes = df["volume"].to_numpy(dtype=np.float64)
        self.date_to_idx: dict[date, int] = {d: i for i, d in enumerate(self.dates)}
        if "price_1500" in df.columns:
            self.prices_1500 = df["price_1500"].to_numpy(dtype=np.float64)
        else:
            self.prices_1500 = self.closes.copy()

    def get_idx(self, d: date) -> Optional[int]:
        return self.date_to_idx.get(d)

    def rolling_avg_volume(self, idx: int, current_date: date, lookback: int, target_start_date: Optional[date] = None) -> tuple[float, bool]:
        """
        Return (mean_volume, has_sufficient_history) for the strictly preceding `lookback` NSE trading days.
        idx itself is EXCLUDED (no lookahead).
        """
        if target_start_date is None:
            target_start_date = prev_trading_day(current_date, lookback)
        
        # Find index in self.dates for target_start_date
        # np.searchsorted returns the index of the first element >= target_start_date
        start_idx = np.searchsorted(self.dates, target_start_date)
        end_idx = idx
        
        prior_vols = self.volumes[start_idx:end_idx]
        
        # The stock must have data for every single one of those `lookback` days
        has_sufficient = (end_idx - start_idx) >= lookback
        if len(prior_vols) == 0:
            return 0.0, False
        return float(prior_vols.mean()), has_sufficient


# ─────────────────────────────────────────────────────────────────────────────
# Main backtest function
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(config: BacktestConfig, progress_callback=None) -> dict:
    """
    Execute a full backtest.
    """
    if config.partial_exit and config.partial_exit.enabled:
        config.partial_exit.validate()

    strategy = config.strategy
    execution = config.execution
    sizing = config.sizing
    fees_cfg = config.fees

    # ── 1. Resolve symbols ─────────────────────────────────────────────────
    symbols = config.symbols if config.symbols else get_available_symbols()
    if not symbols:
        raise ValueError("No symbols available.")

    # ── 2. Load data in parallel ───────────────────────────────────────────
    logger.info(f"Loading {len(symbols)} symbols...")
    if progress_callback:
        progress_callback(f"Loading {len(symbols)} symbols from parquet...")
    raw_data = load_symbols_bulk(symbols, workers=8, progress_callback=progress_callback)
    logger.info(f"Loaded {len(raw_data)} symbols successfully.")

    if progress_callback:
        progress_callback(f"Preparing data structures for {len(raw_data)} symbols...")

    # ── 3. Determine date range ────────────────────────────────────────────
    if config.start_date:
        start_dt = date.fromisoformat(config.start_date)
    else:
        start_dt = min(df["date"].iloc[0] for df in raw_data.values())

    if config.end_date:
        end_dt = date.fromisoformat(config.end_date)
    else:
        end_dt = max(df["date"].iloc[-1] for df in raw_data.values())

    trading_days = get_trading_days(start_dt, end_dt)
    if not trading_days:
        raise ValueError(f"No trading days found between {start_dt} and {end_dt}.")

    logger.info(f"Backtest range: {trading_days[0]} → {trading_days[-1]} ({len(trading_days)} days)")

    # ── 4. Build SymbolData objects ────────────────────────────────────────
    sym_data: dict[str, SymbolData] = {}
    for sym, df in raw_data.items():
        try:
            sym_data[sym] = SymbolData(sym, df)
        except Exception as e:
            logger.warning(f"Failed to build SymbolData for {sym}: {e}")

    # ── 5. Initialize portfolio ────────────────────────────────────────────
    portfolio = Portfolio(sizing.initial_capital)

    # pending_exits: {symbol: (qty, exit_date, pos_entry_price, own_capital, borrowed, entry_fees)}
    # Positions entered on day T will be exited at T+1 open
    pending_exits: dict[str, date] = {}   # symbol → scheduled_exit_date

    signal_log: list[dict] = []
    skipped_entries: list[dict] = []
    total_signals_generated: int = 0
    total_signals_rejected: int = 0

    pending_theoretical_exits: dict[str, tuple[date, float]] = {}  # sym -> (sched_day, entry_price)
    theoretical_closed_returns: list[float] = []

    # ── 6. Main chronological loop ─────────────────────────────────────────
    for day_idx, current_day in enumerate(trading_days):
        if progress_callback and (day_idx % 40 == 0 or day_idx == len(trading_days) - 1):
            pct = int((day_idx + 1) / len(trading_days) * 100)
            progress_callback(f"Simulating: {current_day} ({pct}% | {len(portfolio.closed_trades)} trades)")
        next_day = next_trading_day(current_day)
        daily_fees_accum = 0.0

        # ── 6a. EXIT: close positions scheduled for today ─────────────────
        symbols_to_exit = [
            sym for sym, sched_day in list(pending_exits.items())
            if sched_day == current_day
        ]

        if config.partial_exit and config.partial_exit.enabled:
            for sym in symbols_to_exit:
                sd = sym_data.get(sym)
                if sd is None:
                    logger.error(f"{sym}: Symbol data entirely missing on exit day {current_day}. Trade unexecutable. Trapping capital.")
                    portfolio.mark_unexecutable_trade(sym, current_day, reason="missing_data_entirely")
                    del pending_exits[sym]
                    continue
                exit_idx = sd.get_idx(current_day)
                if exit_idx is None:
                    logger.error(f"{sym}: No data on strict scheduled exit day {current_day}. Trade unexecutable. Trapping capital.")
                    portfolio.mark_unexecutable_trade(sym, current_day, reason="missing_data_on_exit_day")
                    del pending_exits[sym]
                    continue

                pos = portfolio.open_positions.get(sym)
                if pos is None:
                    del pending_exits[sym]
                    continue

                orig_qty = pos.initial_qty if pos.initial_qty > 0 else pos.qty
                first_qty = math.floor(orig_qty * (config.partial_exit.first_pct / 100.0))

                # Leg 1: Exit first_qty at 09:15
                if first_qty > 0:
                    raw_exit_0915 = getattr_field(sd, execution.exit_price_field, exit_idx)
                    exit_price_0915 = apply_slippage(raw_exit_0915, execution.exit_slippage_pct, "sell")
                    exit_trade_value_0915 = exit_price_0915 * first_qty
                    exit_fees_0915 = compute_fees(exit_trade_value_0915, "sell", fees_cfg)
                    portfolio.partial_exit_trade(
                        symbol=sym,
                        exit_date=current_day,
                        exit_price=exit_price_0915,
                        exit_qty=first_qty,
                        exit_fees=exit_fees_0915,
                        exit_time=config.partial_exit.first_time,
                        exit_reason="partial_exit_first",
                        exit_slippage=execution.exit_slippage_pct,
                    )

                # Leg 2: Exit remaining quantity at 15:00
                pos_after = portfolio.open_positions.get(sym)
                rem_qty = pos_after.qty if pos_after else 0
                if rem_qty > 0:
                    raw_exit_1500 = float(sd.prices_1500[exit_idx])
                    exit_price_1500 = apply_slippage(raw_exit_1500, execution.exit_slippage_pct, "sell")
                    exit_trade_value_1500 = exit_price_1500 * rem_qty
                    exit_fees_1500 = compute_fees(exit_trade_value_1500, "sell", fees_cfg)
                    portfolio.partial_exit_trade(
                        symbol=sym,
                        exit_date=current_day,
                        exit_price=exit_price_1500,
                        exit_qty=rem_qty,
                        exit_fees=exit_fees_1500,
                        exit_time=config.partial_exit.second_time,
                        exit_reason="partial_exit_second",
                        exit_slippage=execution.exit_slippage_pct,
                    )

                del pending_exits[sym]
        else:
            for sym in symbols_to_exit:
                sd = sym_data.get(sym)
                if sd is None:
                    logger.error(f"{sym}: Symbol data entirely missing on exit day {current_day}. Trade unexecutable. Trapping capital.")
                    portfolio.mark_unexecutable_trade(sym, current_day, reason="missing_data_entirely")
                    del pending_exits[sym]
                    continue
                exit_idx = sd.get_idx(current_day)
                if exit_idx is None:
                    logger.error(f"{sym}: No data on strict scheduled exit day {current_day}. Trade unexecutable. Trapping capital.")
                    portfolio.mark_unexecutable_trade(sym, current_day, reason="missing_data_on_exit_day")
                    del pending_exits[sym]
                    continue
                
                raw_exit = getattr_field(sd, execution.exit_price_field, exit_idx)
                exit_raw = raw_exit

                # Apply exit slippage (sell)
                exit_price = apply_slippage(exit_raw, execution.exit_slippage_pct, "sell")
                exit_trade_value = exit_price * (portfolio.open_positions.get(sym, None) and portfolio.open_positions[sym].qty or 0)
                exit_fees = compute_fees(exit_trade_value, "sell", fees_cfg)

                portfolio.exit_trade(
                    symbol=sym,
                    exit_date=current_day,
                    exit_price=exit_price,
                    exit_fees=exit_fees,
                    exit_reason="next_open",
                )
                del pending_exits[sym]

        # ── 6a-2. EXIT: close theoretical positions scheduled for today ─────
        symbols_t_exit = [
            sym for sym, (sched_day, _) in pending_theoretical_exits.items()
            if sched_day == current_day
        ]
        for sym in symbols_t_exit:
            _, t_entry_price = pending_theoretical_exits[sym]
            sd = sym_data.get(sym)
            if sd is None:
                del pending_theoretical_exits[sym]
                continue
            exit_idx = sd.get_idx(current_day)
            if exit_idx is None:
                # Theoretical trade is unexecutable. Remove it.
                del pending_theoretical_exits[sym]
                continue
            
            if config.partial_exit and config.partial_exit.enabled:
                t_exit_raw_1 = getattr_field(sd, execution.exit_price_field, exit_idx)
                t_exit_price_1 = apply_slippage(t_exit_raw_1, execution.exit_slippage_pct, "sell")
                t_exit_raw_2 = float(sd.prices_1500[exit_idx])
                t_exit_price_2 = apply_slippage(t_exit_raw_2, execution.exit_slippage_pct, "sell")

                qty = max(1, int(sizing.initial_capital / t_entry_price))
                q1 = math.floor(qty * (config.partial_exit.first_pct / 100.0))
                q2 = qty - q1

                entry_val = qty * t_entry_price
                exit_val = q1 * t_exit_price_1 + q2 * t_exit_price_2
                e_fees = compute_fees(entry_val, "buy", fees_cfg)
                x_fees = compute_fees(q1 * t_exit_price_1, "sell", fees_cfg) + compute_fees(q2 * t_exit_price_2, "sell", fees_cfg)

                net_pnl = (exit_val - entry_val) - e_fees - x_fees
                ret_pct = (net_pnl / entry_val * 100.0) if entry_val > 0 else 0.0
                theoretical_closed_returns.append(ret_pct)
            else:
                t_exit_raw = getattr_field(sd, execution.exit_price_field, exit_idx)
                t_exit_price = apply_slippage(t_exit_raw, execution.exit_slippage_pct, "sell")
                
                # Simulate a trade proportional to initial capital for accurate % fees
                qty = max(1, int(sizing.initial_capital / t_entry_price))
                entry_val = qty * t_entry_price
                exit_val = qty * t_exit_price
                
                e_fees = compute_fees(entry_val, "buy", fees_cfg)
                x_fees = compute_fees(exit_val, "sell", fees_cfg)
                
                net_pnl = (exit_val - entry_val) - e_fees - x_fees
                ret_pct = (net_pnl / entry_val * 100.0) if entry_val > 0 else 0.0
                theoretical_closed_returns.append(ret_pct)

            del pending_theoretical_exits[sym]

        # ── 6b. SIGNAL: evaluate all symbols for today ────────────────────
        signals_today: list[tuple[str, SignalResult, float]] = []  # (sym, sig, entry_price)

        target_start_date = prev_trading_day(current_day, strategy.volume_lookback)
        symbols_today = sorted(sym_data.keys())
        for sym in symbols_today:
            sd = sym_data[sym]
            if sym in portfolio.open_positions:
                continue  # Already holding this symbol

            idx = sd.get_idx(current_day)
            if idx is None:
                continue  # No data today

            # Compute rolling avg volume (prior lookback days only)
            avg_vol, has_sufficient = sd.rolling_avg_volume(idx, current_day, strategy.volume_lookback, target_start_date)

            sig = compute_signal(
                open_=sd.opens[idx],
                high=sd.highs[idx],
                low=sd.lows[idx],
                close=sd.closes[idx],
                volume=sd.volumes[idx],
                avg_volume_20d=avg_vol,
                has_sufficient_history=has_sufficient,
                params=strategy,
            )

            if sig.signal:
                total_signals_generated += 1
                raw_entry = getattr_field(sd, execution.entry_price_field, idx)
                # Apply extra slippage for "close" entry to account for look-ahead uncertainty
                effective_slip = execution.entry_slippage_pct * 2.0 if execution.entry_price_field == "close" else execution.entry_slippage_pct
                entry_price = apply_slippage(raw_entry, effective_slip, "buy")
                signals_today.append((sym, sig, entry_price))

        # ── 6c. ENTRY: size and open positions ────────────────────────────
        # Rank signals by breakout strength score (descending) so trades that
        # break the strategy conditions most strongly take the available slots.
        signals_today.sort(key=lambda x: x[1].score, reverse=True)

        for sym, sig, entry_price in signals_today:
            # Record theoretical trade for "All Signals" metric
            if sym not in pending_theoretical_exits:
                pending_theoretical_exits[sym] = (next_day, entry_price)

            current_equity = portfolio.equity()
            sizing_result = compute_position_size(
                entry_price=entry_price,
                current_equity=current_equity,
                current_cash=portfolio.cash,
                open_positions_count=portfolio.open_positions_count,
                current_gross_exposure=portfolio.gross_exposure,
                avg_volume_20d=sig.avg_volume_20d,
                params=sizing,
                fees_cfg=fees_cfg,
            )

            if sizing_result.skipped:
                total_signals_rejected += 1
                if len(skipped_entries) < 200:
                    skipped_entries.append({
                        "date": str(current_day),
                        "symbol": sym,
                        "reason": sizing_result.skip_reason,
                    })
                continue

            entry_trade_value = sizing_result.qty * entry_price
            entry_fees = compute_fees(entry_trade_value, "buy", fees_cfg)

            success = portfolio.enter_trade(
                symbol=sym,
                entry_date=current_day,
                entry_price=entry_price,
                qty=sizing_result.qty,
                entry_fees=entry_fees,
                own_capital_used=sizing_result.own_capital_used,
                borrowed=sizing_result.borrowed,
                leverage=sizing.leverage,
            )

            if success:
                # Schedule exit for next trading day
                pending_exits[sym] = next_day

        assert portfolio.cash >= -1.0, f"Invariant failed: Cash went negative ({portfolio.cash})"

        # ── 6d. Final-day liquidation (if end of backtest) ─────────────────
        # On the final trading day, liquidate remaining positions at today's close
        # BEFORE taking the final EOD snapshot. This ensures:
        # 1. No lookahead: exit price uses trading_days[-1] close, never data beyond end_date.
        # 2. Synchronized snapshot: final EOD snapshot represents the post-liquidation
        #    portfolio (Final Equity = Final Cash = Initial Capital + Total Net P&L).
        if current_day == trading_days[-1]:
            for sym in list(portfolio.open_positions.keys()):
                pos = portfolio.open_positions[sym]
                sd = sym_data.get(sym)
                idx = sd.get_idx(current_day) if sd else None
                if idx is not None:
                    exit_raw = float(sd.closes[idx])
                else:
                    exit_raw = pos.entry_price
                exit_price = apply_slippage(exit_raw, execution.exit_slippage_pct, "sell")
                exit_fees = compute_fees(exit_price * pos.qty, "sell", fees_cfg)
                if config.partial_exit and config.partial_exit.enabled and pos.partial_exits:
                    portfolio.partial_exit_trade(
                        symbol=sym,
                        exit_date=current_day,
                        exit_price=exit_price,
                        exit_qty=pos.qty,
                        exit_fees=exit_fees,
                        exit_time="15:25",
                        exit_reason="backtest_end",
                        exit_slippage=execution.exit_slippage_pct,
                    )
                else:
                    portfolio.exit_trade(
                        symbol=sym,
                        exit_date=current_day,
                        exit_price=exit_price,
                        exit_fees=exit_fees,
                        exit_reason="backtest_end",
                    )
            pending_exits.clear()

            # Close remaining theoretical trades on final day using final-day close
            for sym, (_, t_entry_price) in list(pending_theoretical_exits.items()):
                sd = sym_data.get(sym)
                idx = sd.get_idx(current_day) if sd else None
                t_exit_raw = float(sd.closes[idx]) if (sd and idx is not None) else t_entry_price
                t_exit_price = apply_slippage(t_exit_raw, execution.exit_slippage_pct, "sell")
                qty = max(1, int(sizing.initial_capital / t_entry_price))
                entry_val = qty * t_entry_price
                exit_val = qty * t_exit_price
                e_fees = compute_fees(entry_val, "buy", fees_cfg)
                x_fees = compute_fees(exit_val, "sell", fees_cfg)
                net_pnl = (exit_val - entry_val) - e_fees - x_fees
                ret_pct = (net_pnl / entry_val * 100.0) if entry_val > 0 else 0.0
                theoretical_closed_returns.append(ret_pct)
            pending_theoretical_exits.clear()

        # ── 6e. Daily snapshot ─────────────────────────────────────────────
        # Mark open positions at today's close for equity calculation
        mark_prices = {}
        for sym in list(portfolio.open_positions.keys()):
            sd = sym_data.get(sym)
            if sd:
                idx = sd.get_idx(current_day)
                if idx is not None:
                    mark_prices[sym] = float(sd.closes[idx])

        portfolio.take_daily_snapshot(current_day, mark_prices)

    # ── 7. Fallback safety: ensure zero open positions at end of backtest ─
    if portfolio.open_positions:
        final_day = trading_days[-1] if trading_days else None
        for sym in list(portfolio.open_positions.keys()):
            pos = portfolio.open_positions[sym]
            sd = sym_data.get(sym)
            idx = sd.get_idx(final_day) if (sd and final_day) else None
            exit_raw = float(sd.closes[idx]) if (sd and idx is not None) else pos.entry_price
            exit_price = apply_slippage(exit_raw, execution.exit_slippage_pct, "sell")
            exit_fees = compute_fees(exit_price * pos.qty, "sell", fees_cfg)
            if config.partial_exit and config.partial_exit.enabled and pos.partial_exits:
                portfolio.partial_exit_trade(
                    symbol=sym,
                    exit_date=final_day or pos.entry_date,
                    exit_price=exit_price,
                    exit_qty=pos.qty,
                    exit_fees=exit_fees,
                    exit_time="15:25",
                    exit_reason="backtest_end",
                    exit_slippage=execution.exit_slippage_pct,
                )
            else:
                portfolio.exit_trade(
                    symbol=sym,
                    exit_date=final_day or pos.entry_date,
                    exit_price=exit_price,
                    exit_fees=exit_fees,
                    exit_reason="backtest_end",
                )
        if portfolio.daily_snapshots:
            portfolio.daily_snapshots[-1].equity = portfolio.cash
            portfolio.daily_snapshots[-1].cash = portfolio.cash
            portfolio.daily_snapshots[-1].open_positions_count = 0
            portfolio.daily_snapshots[-1].holdings = []

    if progress_callback:
        progress_callback("Computing metrics & saving output to output/ folder...")

    # ── 8. Compute metrics ─────────────────────────────────────────────────
    metrics = compute_metrics(
        trades=portfolio.closed_trades,
        snapshots=portfolio.daily_snapshots,
        initial_capital=sizing.initial_capital,
    )
    metrics["total_signals_generated"] = total_signals_generated
    metrics["total_signals_rejected"] = total_signals_rejected

    t_wins = sum(1 for r in theoretical_closed_returns if r > 0)
    t_total = len(theoretical_closed_returns)
    metrics["theoretical_win_rate_pct"] = (t_wins / t_total * 100.0) if t_total > 0 else 0.0
    metrics["theoretical_avg_return_pct"] = (sum(theoretical_closed_returns) / t_total) if t_total > 0 else 0.0
    metrics["theoretical_total_trades"] = t_total

    equities = [s.equity for s in portfolio.daily_snapshots]
    drawdown_series = compute_drawdown_series(equities)

    logger.info(
        f"Backtest complete: {metrics['n_trades']} trades, "
        f"Net P&L ₹{metrics['total_net_pnl']:,.0f}, "
        f"Return {metrics['total_return_pct']:.2f}%"
    )

    result = {
        "metrics": metrics,
        "trades": [t.to_dict() for t in portfolio.closed_trades],
        "daily": [s.to_dict() for s in portfolio.daily_snapshots],
        "drawdown": drawdown_series,
        "equity_dates": [str(s.date) for s in portfolio.daily_snapshots],
        "config": config.to_dict(),
    }

    # Automatically save full output to output/ directory as JSON
    saved_path = save_backtest_output(result)
    result["saved_output_file"] = saved_path

    return result


def save_backtest_output(output_dict: dict) -> str:
    """Save backtest output to the root output/ directory as JSON."""
    try:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        output_dir = os.path.join(base_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"backtest_{timestamp}.json"
        filepath = os.path.join(output_dir, filename)

        class _NumpyEncoder(json.JSONEncoder):
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

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output_dict, f, cls=_NumpyEncoder, indent=2)

        # Also write latest_run.json
        latest_path = os.path.join(output_dir, "latest_run.json")
        try:
            import shutil
            shutil.copyfile(filepath, latest_path)
        except Exception:
            pass

        logger.info(f"Saved full backtest output to {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Failed to save backtest output: {e}")
        return ""


def getattr_field(sd: SymbolData, field: str, idx: int) -> float:
    """Get OHLCV field by name from SymbolData."""
    mapping = {
        "open": sd.opens,
        "high": sd.highs,
        "low": sd.lows,
        "close": sd.closes,
        "price_1500": sd.prices_1500,
    }
    arr = mapping.get(field.lower(), sd.closes)
    return float(arr[idx])
