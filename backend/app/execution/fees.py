"""
Fee calculator: realistic NSE brokerage and transaction costs.
Applied on both entry and exit legs.
"""
from app.strategy.params import FeeParams


def compute_fees(
    trade_value: float,
    side: str,              # "buy" | "sell"
    params: FeeParams,
) -> float:
    """
    Compute total fees for a single trade leg.

    NSE fee structure (approximate):
      Brokerage        : brokerage_pct % of trade value
      Exchange fee     : exchange_fee_pct % of trade value
      GST              : gst_pct % on (brokerage + exchange_fee)
      STT              : stt_pct % of trade value (sell side only)
      SEBI fee         : sebi_fee_pct % of trade value
      Stamp duty       : stamp_duty_pct % of trade value (buy side only)
    """
    brokerage   = trade_value * params.brokerage_pct / 100.0
    exchange_fee = trade_value * params.exchange_fee_pct / 100.0
    gst         = (brokerage + exchange_fee) * params.gst_pct / 100.0
    sebi_fee    = trade_value * params.sebi_fee_pct / 100.0
    stt         = trade_value * params.stt_pct / 100.0 if side == "sell" else 0.0
    stamp       = trade_value * params.stamp_duty_pct / 100.0 if side == "buy" else 0.0

    return brokerage + exchange_fee + gst + stt + sebi_fee + stamp


def apply_slippage(price: float, slippage_pct: float, side: str) -> float:
    """
    Apply slippage to price.
    Buy:  price increases  (unfavorable)
    Sell: price decreases  (unfavorable)
    """
    factor = slippage_pct / 100.0
    if side == "buy":
        return price * (1 + factor)
    else:
        return price * (1 - factor)
