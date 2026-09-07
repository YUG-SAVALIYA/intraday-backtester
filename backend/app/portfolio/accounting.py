"""
Portfolio accounting: cash ledger, open positions, P&L, equity curve.

Key invariants:
- Cash never goes negative (trades are rejected if insufficient cash)
- Equity = Cash + sum(open_position_values_at_close)
- All fees deducted from cash at trade time
- Borrowed capital is tracked separately (for leverage reporting)
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Position:
    symbol: str
    entry_date: date
    entry_price: float          # Price paid (after slippage)
    qty: int
    cost_basis: float           # entry_price × qty (gross)
    entry_fees: float           # Fees paid at entry
    own_capital_used: float     # Own money allocated
    borrowed: float             # Margin borrowed
    leverage: float


@dataclass
class ClosedTrade:
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    qty: int
    gross_pnl: float            # (exit - entry) × qty
    entry_fees: float
    exit_fees: float
    net_pnl: float              # gross_pnl - entry_fees - exit_fees
    return_pct: float           # net_pnl / (entry_price × qty) × 100
    own_capital_used: float
    borrowed: float
    leverage: float
    exit_reason: str            # "next_open"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "entry_date": str(self.entry_date),
            "exit_date": str(self.exit_date),
            "entry_price": round(self.entry_price, 4),
            "exit_price": round(self.exit_price, 4),
            "qty": self.qty,
            "gross_pnl": round(self.gross_pnl, 2),
            "entry_fees": round(self.entry_fees, 2),
            "exit_fees": round(self.exit_fees, 2),
            "net_pnl": round(self.net_pnl, 2),
            "return_pct": round(self.return_pct, 4),
            "own_capital_used": round(self.own_capital_used, 2),
            "borrowed": round(self.borrowed, 2),
            "leverage": round(self.leverage, 2),
            "exit_reason": self.exit_reason,
        }


@dataclass
class DailySnapshot:
    date: date
    equity: float
    cash: float
    invested_capital: float
    open_positions_count: int
    gross_exposure: float
    realized_pnl: float         # Cumulative realized P&L up to this day
    daily_pnl: float            # Equity change on this day (equity - prev_equity)
    daily_pnl_pct: float        # Daily return % (equity change / prev_equity * 100)
    realized_pnl_today: float   # Realized P&L from positions closed today
    fees_paid_today: float
    drawdown_pct: float         # Drawdown % from peak equity (negative number, e.g. -2.35%)
    drawdown_abs: float         # Absolute rupee drawdown from peak equity
    buys: list[dict] = field(default_factory=list)
    sells: list[dict] = field(default_factory=list)
    holdings: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "date": str(self.date),
            "equity": round(self.equity, 2),
            "cash": round(self.cash, 2),
            "invested_capital": round(self.invested_capital, 2),
            "open_positions_count": self.open_positions_count,
            "gross_exposure": round(self.gross_exposure, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_pnl_pct": round(self.daily_pnl_pct, 4),
            "realized_pnl_today": round(self.realized_pnl_today, 2),
            "fees_paid_today": round(self.fees_paid_today, 2),
            "drawdown_pct": round(self.drawdown_pct, 4),
            "drawdown_abs": round(self.drawdown_abs, 2),
            "buys": self.buys,
            "sells": self.sells,
            "holdings": self.holdings,
        }


class Portfolio:
    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.cash: float = initial_capital
        self.open_positions: dict[str, Position] = {}  # symbol → Position
        self.closed_trades: list[ClosedTrade] = []
        self.daily_snapshots: list[DailySnapshot] = []
        self.cumulative_realized_pnl: float = 0.0
        self.total_fees_paid: float = 0.0
        self._daily_fees: float = 0.0
        self._daily_pnl: float = 0.0
        self._daily_buys: list[dict] = []
        self._daily_sells: list[dict] = []
        self.peak_equity: float = initial_capital
        self.prev_equity: float = initial_capital

    @property
    def open_positions_count(self) -> int:
        return len(self.open_positions)

    @property
    def gross_exposure(self) -> float:
        return sum(p.cost_basis for p in self.open_positions.values())

    def equity(self, mark_prices: dict[str, float] = None) -> float:
        """
        Current portfolio equity.
        If mark_prices provided (symbol→close), uses them for unrealized P&L.
        Otherwise uses cost basis as proxy.
        """
        if mark_prices is None:
            return self.cash + self.gross_exposure - sum(p.borrowed for p in self.open_positions.values())
        unrealized = sum(
            p.qty * mark_prices.get(sym, p.entry_price)
            for sym, p in self.open_positions.items()
        )
        return self.cash + unrealized - sum(p.borrowed for p in self.open_positions.values())

    def enter_trade(
        self,
        symbol: str,
        entry_date: date,
        entry_price: float,
        qty: int,
        entry_fees: float,
        own_capital_used: float,
        borrowed: float,
        leverage: float,
    ) -> bool:
        """
        Open a new position. Returns True if successful.
        Cash is reduced by own_capital_used + entry_fees.
        """
        if symbol in self.open_positions:
            return False  # Already open

        total_cash_needed = own_capital_used + entry_fees
        if self.cash < total_cash_needed - 0.01:
            return False  # Insufficient cash

        self.cash -= total_cash_needed
        self.total_fees_paid += entry_fees
        self._daily_fees += entry_fees

        self.open_positions[symbol] = Position(
            symbol=symbol,
            entry_date=entry_date,
            entry_price=entry_price,
            qty=qty,
            cost_basis=qty * entry_price,
            entry_fees=entry_fees,
            own_capital_used=own_capital_used,
            borrowed=borrowed,
            leverage=leverage,
        )

        self._daily_buys.append({
            "symbol": symbol,
            "entry_date": str(entry_date),
            "entry_time": "15:20",
            "entry_price": round(entry_price, 4),
            "qty": qty,
            "trade_value": round(qty * entry_price, 2),
            "own_capital_used": round(own_capital_used, 2),
            "entry_fees": round(entry_fees, 2),
            "borrowed": round(borrowed, 2),
            "leverage": round(leverage, 2),
        })

        return True

    def exit_trade(
        self,
        symbol: str,
        exit_date: date,
        exit_price: float,
        exit_fees: float,
        exit_reason: str = "next_open",
    ) -> Optional[ClosedTrade]:
        """
        Close an open position. Returns ClosedTrade or None if not found.
        Cash is credited with (qty × exit_price - exit_fees + borrowed repaid).
        """
        pos = self.open_positions.pop(symbol, None)
        if pos is None:
            return None

        gross_pnl = (exit_price - pos.entry_price) * pos.qty
        net_pnl = gross_pnl - pos.entry_fees - exit_fees
        return_pct = net_pnl / pos.cost_basis * 100 if pos.cost_basis > 0 else 0.0

        # Credit cash: proceeds + repay borrowed
        proceeds = pos.qty * exit_price - exit_fees
        # Repay borrowed (out of proceeds)
        cash_returned = proceeds - pos.borrowed
        self.cash += max(0.0, cash_returned)

        self.total_fees_paid += exit_fees
        self._daily_fees += exit_fees
        self.cumulative_realized_pnl += net_pnl
        self._daily_pnl += net_pnl

        trade = ClosedTrade(
            symbol=symbol,
            entry_date=pos.entry_date,
            exit_date=exit_date,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            qty=pos.qty,
            gross_pnl=gross_pnl,
            entry_fees=pos.entry_fees,
            exit_fees=exit_fees,
            net_pnl=net_pnl,
            return_pct=return_pct,
            own_capital_used=pos.own_capital_used,
            borrowed=pos.borrowed,
            leverage=pos.leverage,
            exit_reason=exit_reason,
        )
        self.closed_trades.append(trade)

        self._daily_sells.append({
            "symbol": symbol,
            "entry_date": str(pos.entry_date),
            "entry_time": "15:20",
            "exit_date": str(exit_date),
            "exit_time": "09:15",
            "entry_price": round(pos.entry_price, 4),
            "exit_price": round(exit_price, 4),
            "qty": pos.qty,
            "gross_pnl": round(gross_pnl, 2),
            "entry_fees": round(pos.entry_fees, 2),
            "exit_fees": round(exit_fees, 2),
            "total_fees": round(pos.entry_fees + exit_fees, 2),
            "net_pnl": round(net_pnl, 2),
            "return_pct": round(return_pct, 4),
            "own_capital_used": round(pos.own_capital_used, 2),
            "exit_reason": exit_reason,
        })

        return trade

    def take_daily_snapshot(self, d: date, mark_prices: dict[str, float] = None) -> None:
        """Record end-of-day portfolio state."""
        eq = self.equity(mark_prices)

        # Calculate daily pnl & return vs prev_equity
        daily_pnl = eq - self.prev_equity
        daily_pnl_pct = (daily_pnl / self.prev_equity * 100) if self.prev_equity > 0 else 0.0

        # Peak equity & Drawdown
        if eq > self.peak_equity:
            self.peak_equity = eq
        dd_abs = self.peak_equity - eq
        dd_pct = -(dd_abs / self.peak_equity * 100) if self.peak_equity > 0 else 0.0

        # Invested capital (own money allocated in open positions)
        invested_cap = sum(p.own_capital_used for p in self.open_positions.values())

        # Holdings snapshot
        holdings = []
        if mark_prices is None:
            mark_prices = {}
        for sym, pos in self.open_positions.items():
            mkt_price = mark_prices.get(sym, pos.entry_price)
            curr_val = pos.qty * mkt_price
            unrealized_pnl = curr_val - pos.cost_basis
            unrealized_pct = (unrealized_pnl / pos.cost_basis * 100) if pos.cost_basis > 0 else 0.0
            holdings.append({
                "symbol": sym,
                "qty": pos.qty,
                "entry_date": str(pos.entry_date),
                "entry_time": "15:20",
                "entry_price": round(pos.entry_price, 4),
                "current_price": round(mkt_price, 4),
                "cost_basis": round(pos.cost_basis, 2),
                "current_value": round(curr_val, 2),
                "own_capital_used": round(pos.own_capital_used, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "unrealized_return_pct": round(unrealized_pct, 4),
            })

        snap = DailySnapshot(
            date=d,
            equity=eq,
            cash=self.cash,
            invested_capital=invested_cap,
            open_positions_count=self.open_positions_count,
            gross_exposure=self.gross_exposure,
            realized_pnl=self.cumulative_realized_pnl,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            realized_pnl_today=self._daily_pnl,
            fees_paid_today=self._daily_fees,
            drawdown_pct=dd_pct,
            drawdown_abs=dd_abs,
            buys=list(self._daily_buys),
            sells=list(self._daily_sells),
            holdings=holdings,
        )
        self.daily_snapshots.append(snap)

        # Advance prev_equity to today's equity
        self.prev_equity = eq

        # Reset daily counters
        self._daily_pnl = 0.0
        self._daily_fees = 0.0
        self._daily_buys = []
        self._daily_sells = []
