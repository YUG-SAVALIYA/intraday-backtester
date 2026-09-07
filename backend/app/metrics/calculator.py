"""
Performance metrics calculator.
Inputs: list of ClosedTrade and list of DailySnapshot.
All math is explicit — no black-box libraries.
"""
import math
from typing import Optional
from app.portfolio.accounting import ClosedTrade, DailySnapshot


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def compute_metrics(
    trades: list[ClosedTrade],
    snapshots: list[DailySnapshot],
    initial_capital: float,
) -> dict:
    """
    Compute full set of performance metrics.

    Returns a dict with all stats as plain numbers (JSON-serializable).
    """
    if not trades and not snapshots:
        return _empty_metrics(initial_capital)

    # ── Basic trade stats ──────────────────────────────────────────────────
    n_trades = len(trades)
    pnls = [t.net_pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_rate = len(wins) / n_trades * 100 if n_trades else 0
    avg_trade = _safe_div(sum(pnls), n_trades)
    avg_win = _safe_div(sum(wins), len(wins))
    avg_loss = _safe_div(sum(losses), len(losses))

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = _safe_div(gross_profit, gross_loss)

    total_net_pnl = sum(pnls)
    total_fees = sum(t.entry_fees + t.exit_fees for t in trades)

    # ── Equity curve from snapshots ──────────────────────────────────────
    if snapshots:
        final_equity = snapshots[-1].equity
        equities = [s.equity for s in snapshots]

        # Max drawdown
        max_dd_pct, max_dd_abs = _compute_max_drawdown(equities)

        # CAGR
        start_date = snapshots[0].date
        end_date = snapshots[-1].date
        years = max((end_date - start_date).days / 365.25, 1 / 365.25)
        total_return_pct = _safe_div(final_equity - initial_capital, initial_capital) * 100
        cagr = (math.pow(final_equity / initial_capital, 1.0 / years) - 1.0) * 100 if initial_capital > 0 else 0

        # Sharpe ratio (daily returns, annualized)
        sharpe = _compute_sharpe(snapshots)

        # Calmar
        calmar = _safe_div(cagr, max_dd_pct) if max_dd_pct > 0 else 0
        
        # Periodic Returns
        periodic_returns = _compute_periodic_returns(snapshots)
    else:
        final_equity = initial_capital + total_net_pnl
        max_dd_pct = max_dd_abs = 0.0
        cagr = 0.0
        total_return_pct = _safe_div(total_net_pnl, initial_capital) * 100
        sharpe = 0.0
        calmar = 0.0
        periodic_returns = {"daily": [], "weekly": [], "monthly": [], "yearly": []}

    # ── Consecutive win/loss streaks ─────────────────────────────────────
    max_win_streak = max_loss_streak = 0
    cur_win = cur_loss = 0
    for p in pnls:
        if p > 0:
            cur_win += 1; cur_loss = 0
        else:
            cur_loss += 1; cur_win = 0
        max_win_streak = max(max_win_streak, cur_win)
        max_loss_streak = max(max_loss_streak, cur_loss)

    return {
        "n_trades": n_trades,
        "n_wins": len(wins),
        "n_losses": len(losses),
        "win_rate_pct": round(win_rate, 2),
        "avg_trade": round(avg_trade, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(profit_factor, 4),
        "total_net_pnl": round(total_net_pnl, 2),
        "total_fees": round(total_fees, 2),
        "initial_capital": round(initial_capital, 2),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return_pct, 4),
        "cagr_pct": round(cagr, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "max_drawdown_abs": round(max_dd_abs, 2),
        "sharpe_ratio": round(sharpe, 4),
        "calmar_ratio": round(calmar, 4),
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "avg_return_pct_per_trade": round(
            _safe_div(sum(t.return_pct for t in trades), n_trades), 4
        ),
        "periodic_returns": periodic_returns,
    }


def _compute_max_drawdown(equities: list[float]) -> tuple[float, float]:
    """Return (max_dd_pct, max_dd_abs)."""
    if not equities:
        return 0.0, 0.0
    peak = equities[0]
    max_dd_abs = 0.0
    max_dd_pct = 0.0
    for eq in equities:
        if eq > peak:
            peak = eq
        dd_abs = peak - eq
        dd_pct = dd_abs / peak * 100 if peak > 0 else 0
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd_abs = dd_abs
    return max_dd_pct, max_dd_abs


def compute_drawdown_series(equities: list[float]) -> list[float]:
    """Return per-day drawdown % from peak (negative values)."""
    result = []
    peak = 0.0
    for eq in equities:
        if eq > peak:
            peak = eq
        dd = -(peak - eq) / peak * 100 if peak > 0 else 0.0
        result.append(round(dd, 4))
    return result


def _compute_sharpe(snapshots: list[DailySnapshot], risk_free_annual: float = 0.065) -> float:
    """Annualized Sharpe using daily equity returns."""
    if len(snapshots) < 2:
        return 0.0
    equities = [s.equity for s in snapshots]
    daily_returns = [
        (equities[i] - equities[i - 1]) / equities[i - 1]
        for i in range(1, len(equities))
        if equities[i - 1] > 0
    ]
    if len(daily_returns) < 2:
        return 0.0
    n = len(daily_returns)
    mean_r = sum(daily_returns) / n
    variance = sum((r - mean_r) ** 2 for r in daily_returns) / (n - 1)
    std_r = math.sqrt(variance) if variance > 0 else 0.0
    if std_r == 0:
        return 0.0
    risk_free_daily = (1 + risk_free_annual) ** (1 / 252) - 1
    return (mean_r - risk_free_daily) / std_r * math.sqrt(252)


def _empty_metrics(initial_capital: float) -> dict:
    return {
        "n_trades": 0, "n_wins": 0, "n_losses": 0,
        "win_rate_pct": 0, "avg_trade": 0, "avg_win": 0, "avg_loss": 0,
        "gross_profit": 0, "gross_loss": 0, "profit_factor": 0,
        "total_net_pnl": 0, "total_fees": 0,
        "initial_capital": initial_capital, "final_equity": initial_capital,
        "total_return_pct": 0, "cagr_pct": 0,
        "max_drawdown_pct": 0, "max_drawdown_abs": 0,
        "sharpe_ratio": 0, "calmar_ratio": 0,
        "max_win_streak": 0, "max_loss_streak": 0,
        "avg_return_pct_per_trade": 0,
        "periodic_returns": {"daily": [], "weekly": [], "monthly": [], "yearly": []},
    }


def _compute_periodic_returns(snapshots: list[DailySnapshot]) -> dict:
    import pandas as pd
    if not snapshots:
        return {"daily": [], "weekly": [], "monthly": [], "yearly": []}
    
    dates = [s.date for s in snapshots]
    equities = [s.equity for s in snapshots]
    
    df = pd.DataFrame({"date": pd.to_datetime(dates), "equity": equities})
    df.set_index("date", inplace=True)
    
    # Calculate period returns by taking the equity at the end of each period
    # vs the equity at the end of the previous period.
    
    def calculate_returns(resampled_series):
        # resampled_series is equity at period end
        returns = resampled_series.pct_change() * 100
        # drop NaN
        returns = returns.dropna()
        # format back to strings
        result = []
        for d, r in returns.items():
            result.append({"period": d.strftime("%Y-%m-%d"), "return_pct": round(r, 4)})
        return result

    # Daily
    daily = calculate_returns(df["equity"])
    
    # Weekly (W-FRI)
    weekly_eq = df["equity"].resample("W-FRI").last().dropna()
    weekly = []
    for d, r in (weekly_eq.pct_change() * 100).dropna().items():
        weekly.append({"period": f"Week of {d.strftime('%Y-%m-%d')}", "return_pct": round(r, 4)})
        
    # Monthly
    monthly_eq = df["equity"].resample("ME").last().dropna()
    monthly = []
    for d, r in (monthly_eq.pct_change() * 100).dropna().items():
        monthly.append({"period": d.strftime("%Y-%b"), "return_pct": round(r, 4)})
        
    # Yearly
    yearly_eq = df["equity"].resample("YE").last().dropna()
    yearly = []
    for d, r in (yearly_eq.pct_change() * 100).dropna().items():
        yearly.append({"period": d.strftime("%Y"), "return_pct": round(r, 4)})
        
    return {
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "yearly": yearly
    }
