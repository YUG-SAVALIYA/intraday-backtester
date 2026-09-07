"""
Strategy parameter definitions (fully parameterized, no hardcoded values).
All default values match the strategy specification exactly.
"""
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class StrategyParams:
    """Signal generation parameters."""
    # Signal thresholds
    min_range_pct: float = 10.0        # Range % >= this
    min_body_pct: float = 5.0          # Bullish body % > this
    min_close_loc_pct: float = 90.0    # Close location % >= this

    # Volume filter
    volume_lookback: int = 20          # Number of prior trading days for avg volume
    volume_multiplier: float = 2.0     # Today volume > multiplier × avg volume


@dataclass
class ExecutionParams:
    """Trade execution configuration."""
    # Entry: price to use on signal day (15:25 close)
    entry_price_field: str = "close"   # "close", "open", "high", "low"
    entry_slippage_pct: float = 0.05   # % slippage on entry (0.05 = 5 bps)

    # Exit: next trading day open
    exit_price_field: str = "open"     # field from next day row
    exit_slippage_pct: float = 0.05    # % slippage on exit


@dataclass
class SizingParams:
    """Position sizing and capital management."""
    initial_capital: float = 100_000.0  # ₹1 lakh

    # Per-trade allocation
    allocation_type: str = "pct"       # "pct" | "fixed"
    allocation_value: float = 20.0     # % of equity per trade (or fixed ₹ amount)

    # Portfolio limits
    max_positions: int = 5             # Max simultaneous open trades
    max_exposure_pct: float = 100.0    # Max gross exposure as % of equity
    
    # Liquidity limits
    max_volume_pct: float = 5.0        # Max % of 20-day avg volume allowed per trade

    # Leverage
    leverage: float = 1.0             # 1.0 = no leverage

    # Reinvestment
    reinvest: bool = True              # Compound returns into position sizes


@dataclass
class FeeParams:
    """Brokerage and transaction costs."""
    brokerage_pct: float = 0.03        # % of trade value (both legs)
    stt_pct: float = 0.025             # Securities Transaction Tax (sell side)
    exchange_fee_pct: float = 0.00345  # NSE exchange fee
    gst_pct: float = 18.0             # GST on (brokerage + exchange fee)
    sebi_fee_pct: float = 0.0001       # SEBI turnover fee
    stamp_duty_pct: float = 0.015      # Stamp duty (buy side)


@dataclass
class BacktestConfig:
    """Full backtest configuration — the single object passed through the pipeline."""
    strategy: StrategyParams = field(default_factory=StrategyParams)
    execution: ExecutionParams = field(default_factory=ExecutionParams)
    sizing: SizingParams = field(default_factory=SizingParams)
    fees: FeeParams = field(default_factory=FeeParams)

    # Date range
    start_date: Optional[str] = None   # ISO date string "YYYY-MM-DD"
    end_date: Optional[str] = None

    # Symbols (empty = all available)
    symbols: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BacktestConfig":
        cfg = cls()
        if "strategy" in d:
            cfg.strategy = StrategyParams(**d["strategy"])
        if "execution" in d:
            cfg.execution = ExecutionParams(**d["execution"])
        if "sizing" in d:
            cfg.sizing = SizingParams(**d["sizing"])
        if "fees" in d:
            cfg.fees = FeeParams(**d["fees"])
        cfg.start_date = d.get("start_date")
        cfg.end_date = d.get("end_date")
        cfg.symbols = d.get("symbols", [])
        return cfg
