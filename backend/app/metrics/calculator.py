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

        # Max drawdown (from authoritative daily EOD equity series)
        max_dd_pct, max_dd_abs = _compute_max_drawdown(equities)

        # CAGR
        start_date = snapshots[0].date
        end_date = snapshots[-1].date
        years = max((end_date - start_date).days / 365.25, 1 / 365.25)
        total_return_pct = _safe_div(final_equity - initial_capital, initial_capital) * 100
        # Guard: math.pow raises ValueError if base is negative or zero (wiped-out portfolio)
        if initial_capital > 0 and final_equity > 0:
            cagr = (math.pow(final_equity / initial_capital, 1.0 / years) - 1.0) * 100
        else:
            cagr = -100.0 if final_equity <= 0 else 0.0

        # Sharpe ratio (daily returns, annualized)
        sharpe = _compute_sharpe(snapshots, initial_capital)

        # Calmar
        calmar = _safe_div(cagr, max_dd_pct) if max_dd_pct > 0 else 0
        
        # Periodic Returns
        periodic_returns = _compute_periodic_returns(snapshots, initial_capital)
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
    """
    Compute daily EOD maximum drawdown from authoritative daily EOD equity series.
    Returns (max_dd_pct, max_dd_abs) where max_dd_pct is positive magnitude (e.g. 20.14)
    matching the existing reporting convention.
    """
    if not equities:
        return 0.0, 0.0
    peak = equities[0]
    max_dd_abs = 0.0
    max_dd_pct = 0.0
    for eq in equities:
        if eq > peak:
            peak = eq
        dd_abs = peak - eq
        dd_pct = (dd_abs / peak * 100.0) if peak > 0 else 0.0
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd_abs = dd_abs
    return max_dd_pct, max_dd_abs


def compute_drawdown_series(equities: list[float]) -> list[float]:
    """Return per-day drawdown % from peak (negative values)."""
    if not equities:
        return []
    result = []
    peak = equities[0]
    for eq in equities:
        if eq > peak:
            peak = eq
        dd = -(peak - eq) / peak * 100 if peak > 0 else 0.0
        result.append(round(dd, 4))
    return result


def _compute_sharpe(snapshots: list[DailySnapshot], initial_capital: float = 0.0, risk_free_annual: float = 0.065) -> float:
    """Annualized Sharpe using daily equity returns."""
    if len(snapshots) < 1:
        return 0.0
    equities = ([initial_capital] if initial_capital > 0 else []) + [s.equity for s in snapshots]
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


def _compute_periodic_returns(snapshots: list[DailySnapshot], initial_capital: float = 0.0) -> dict:
    """
    Compute daily, weekly, monthly, and yearly returns from chronological daily snapshots.
    Guarantees no missing initial periods (e.g. first year, first month, first week)
    by correctly referencing initial_capital as starting equity for the first period
    and prior period's ending equity for subsequent periods.
    """
    if not snapshots:
        return {"daily": [], "weekly": [], "monthly": [], "yearly": []}

    from collections import defaultdict

    # Ensure snapshots are sorted chronologically
    sorted_snaps = sorted(snapshots, key=lambda s: s.date)

    # ── Daily returns ──────────────────────────────────────────────────
    daily = [
        {"period": str(s.date), "return_pct": round(s.daily_pnl_pct, 4)}
        for s in sorted_snaps
    ]

    # ── Weekly returns (grouped by ISO calendar week) ──────────────────
    weekly_groups = defaultdict(list)
    for s in sorted_snaps:
        iso_year, iso_week, _ = s.date.isocalendar()
        weekly_groups[(iso_year, iso_week)].append(s)

    weekly = []
    prev_week_equity = initial_capital
    # Sort by (iso_year, iso_week) to guarantee chronological order
    for _, sn_list in sorted(weekly_groups.items()):
        end_equity = sn_list[-1].equity
        ret_pct = ((end_equity - prev_week_equity) / prev_week_equity * 100.0) if prev_week_equity > 0 else 0.0
        start_d = sn_list[0].date
        weekly.append({
            "period": f"Week of {start_d.strftime('%Y-%m-%d')}",
            "return_pct": round(ret_pct, 4),
            "start_equity": round(prev_week_equity, 2),
            "end_equity": round(end_equity, 2),
        })
        prev_week_equity = end_equity

    # ── Monthly returns (grouped by calendar month) ───────────────────
    monthly_groups = defaultdict(list)
    for s in sorted_snaps:
        monthly_groups[(s.date.year, s.date.month)].append(s)

    monthly = []
    prev_month_equity = initial_capital
    # Sort by (year, month) to guarantee chronological order
    for _, sn_list in sorted(monthly_groups.items()):
        end_equity = sn_list[-1].equity
        ret_pct = ((end_equity - prev_month_equity) / prev_month_equity * 100.0) if prev_month_equity > 0 else 0.0
        start_d = sn_list[0].date
        monthly.append({
            "period": start_d.strftime("%Y-%b"),
            "return_pct": round(ret_pct, 4),
            "start_equity": round(prev_month_equity, 2),
            "end_equity": round(end_equity, 2),
        })
        prev_month_equity = end_equity

    # ── Yearly returns (grouped by calendar year) ──────────────────────
    yearly_groups = defaultdict(list)
    for s in sorted_snaps:
        yearly_groups[s.date.year].append(s)

    yearly = []
    prev_year_equity = initial_capital
    for y in sorted(yearly_groups.keys()):
        sn_list = yearly_groups[y]
        end_equity = sn_list[-1].equity
        ret_pct = ((end_equity - prev_year_equity) / prev_year_equity * 100.0) if prev_year_equity > 0 else 0.0
        yearly.append({
            "period": str(y),
            "return_pct": round(ret_pct, 4),
            "start_equity": round(prev_year_equity, 2),
            "end_equity": round(end_equity, 2),
        })
        prev_year_equity = end_equity

    return {
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "yearly": yearly
    }
