"""
Position sizing and capital allocation.

Rules:
- Per-trade allocated capital = pct of current equity OR fixed ₹ amount
- Quantity = floor(allocated_capital × leverage / entry_price)
- Max simultaneous positions enforced BEFORE new entry
- Max portfolio exposure (gross_exposure / equity) enforced BEFORE new entry
- Leverage: borrowing = qty × price × (1 - 1/leverage)
- Own capital used = qty × price / leverage
"""
import math
from dataclasses import dataclass

from app.strategy.params import SizingParams, FeeParams
from app.execution.fees import compute_fees


@dataclass
class SizingResult:
    qty: int                    # Number of shares to buy
    allocated_capital: float    # Capital requested for this trade
    own_capital_used: float     # Own money deployed (allocated / leverage)
    borrowed: float             # Margin/borrowed amount
    gross_exposure: float       # qty × entry_price
    entry_price_with_slip: float
    skipped: bool = False       # True if position was skipped due to limits
    skip_reason: str = ""


def compute_position_size(
    entry_price: float,
    current_equity: float,
    current_cash: float,
    open_positions_count: int,
    current_gross_exposure: float,
    avg_volume_20d: float,
    params: SizingParams,
    fees_cfg: FeeParams = FeeParams(),
) -> SizingResult:
    """
    Calculate position size given current portfolio state.

    Parameters
    ----------
    entry_price : float          Actual fill price (after slippage)
    current_equity : float       Mark-to-market portfolio equity
    current_cash : float         Available uninvested cash
    open_positions_count : int   Currently open trades
    current_gross_exposure : float  Sum of all open position values
    avg_volume_20d : float       20-day average volume
    params : SizingParams        Sizing configuration
    fees_cfg : FeeParams         Fee configuration for exact cash requirements

    Returns
    -------
    SizingResult with qty=0 and skipped=True if limits are exceeded
    """
    if entry_price <= 0:
        return SizingResult(
            qty=0, allocated_capital=0, own_capital_used=0,
            borrowed=0, gross_exposure=0,
            entry_price_with_slip=entry_price,
            skipped=True, skip_reason="Invalid entry price"
        )

    # ── Check max positions cap ──────────────────────────────────────────
    if open_positions_count >= params.max_positions:
        return SizingResult(
            qty=0, allocated_capital=0, own_capital_used=0,
            borrowed=0, gross_exposure=0,
            entry_price_with_slip=entry_price,
            skipped=True, skip_reason=f"Max positions ({params.max_positions}) reached"
        )

    # ── Determine per-trade allocation ───────────────────────────────────
    if params.allocation_type == "pct":
        base_equity = current_equity if params.reinvest else params.initial_capital
        allocated = base_equity * params.allocation_value / 100.0
    else:
        allocated = params.allocation_value

    cash_budget = min(allocated, current_cash)  # can't exceed cash

    # Cash cost per share including buy fees and leverage
    exact_fees_per_share = compute_fees(entry_price, "buy", fees_cfg) if fees_cfg else 0.0
    cash_cost_per_share = (entry_price / params.leverage) + exact_fees_per_share

    # Maximum shares we can afford with the allocated cash budget
    qty = math.floor(cash_budget / cash_cost_per_share) if cash_cost_per_share > 0 else 0

    if qty == 0:
        return SizingResult(
            qty=0, allocated_capital=0, own_capital_used=0,
            borrowed=0, gross_exposure=0,
            entry_price_with_slip=entry_price,
            skipped=True, skip_reason="INSUFFICIENT_ALLOCATED_CAPITAL"
        )

    # ── Check max volume limit (only when explicitly configured > 0) ─────
    if avg_volume_20d > 0 and params.max_volume_pct > 0:
        max_shares_by_vol = math.floor(avg_volume_20d * (params.max_volume_pct / 100.0))
        qty = min(qty, max_shares_by_vol)

    if qty == 0:
        return SizingResult(
            qty=0, allocated_capital=0, own_capital_used=0,
            borrowed=0, gross_exposure=0,
            entry_price_with_slip=entry_price,
            skipped=True, skip_reason="INSUFFICIENT_ALLOCATED_CAPITAL"
        )

    gross_this_trade = qty * entry_price

    # ── Check max portfolio exposure ─────────────────────────────────────
    max_allowed_exposure = current_equity * params.max_exposure_pct / 100.0
    if current_gross_exposure + gross_this_trade > max_allowed_exposure:
        remaining_exposure = max_allowed_exposure - current_gross_exposure
        if remaining_exposure <= 0:
            return SizingResult(
                qty=0, allocated_capital=0, own_capital_used=0,
                borrowed=0, gross_exposure=0,
                entry_price_with_slip=entry_price,
                skipped=True, skip_reason="Max portfolio exposure reached"
            )
        qty = max(0, math.floor(remaining_exposure / entry_price))
        if qty == 0:
            return SizingResult(
                qty=0, allocated_capital=0, own_capital_used=0,
                borrowed=0, gross_exposure=0,
                entry_price_with_slip=entry_price,
                skipped=True, skip_reason="Max portfolio exposure: no room for 1 share"
            )
        gross_this_trade = qty * entry_price

    own_capital_used = gross_this_trade / params.leverage
    borrowed = gross_this_trade - own_capital_used

    # Final check: enough cash for own capital portion + fees
    actual_buy_fees = compute_fees(gross_this_trade, "buy", fees_cfg) if fees_cfg else 0.0
    if own_capital_used + actual_buy_fees > current_cash + 1.0:  # 1 rupee tolerance
        return SizingResult(
            qty=0, allocated_capital=0, own_capital_used=0,
            borrowed=0, gross_exposure=0,
            entry_price_with_slip=entry_price,
            skipped=True, skip_reason="Insufficient cash"
        )

    return SizingResult(
        qty=qty,
        allocated_capital=gross_this_trade,
        own_capital_used=own_capital_used,
        borrowed=borrowed,
        gross_exposure=gross_this_trade,
        entry_price_with_slip=entry_price,
        skipped=False,
    )
