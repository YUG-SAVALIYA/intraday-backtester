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
    volume_multiplier: float = 2.5     # Optimized: 2.5x volume spike


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
    allocation_value: float = 15.0     # Optimized: 15% of equity per trade

    # Portfolio limits
    max_positions: int = 3             # Optimized: Max 3 simultaneous open trades
    max_exposure_pct: float = 100.0    # Max gross exposure as % of equity
    
    # Liquidity limits
    max_volume_pct: float = 0.0        # Max % of 20-day avg volume allowed per trade (0.0 = disabled)

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


import math
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class PartialExitParams:
    """Configuration for optional partial exits."""
    partial_exit_enabled: bool = False
    partial_exit_first_pct: float = 50.0
    partial_exit_first_time: str = "09:15"
    partial_exit_second_pct: float = 50.0
    partial_exit_second_time: str = "15:00"

    def __init__(
        self,
        partial_exit_enabled: Optional[bool] = None,
        partial_exit_first_pct: Optional[float] = None,
        partial_exit_first_time: Optional[str] = None,
        partial_exit_second_pct: Optional[float] = None,
        partial_exit_second_time: Optional[str] = None,
        enabled: Optional[bool] = None,
        first_pct: Optional[float] = None,
        first_time: Optional[str] = None,
        second_pct: Optional[float] = None,
        second_time: Optional[str] = None,
    ):
        if partial_exit_enabled is not None:
            self.partial_exit_enabled = partial_exit_enabled
        elif enabled is not None:
            self.partial_exit_enabled = enabled
        else:
            self.partial_exit_enabled = False

        if partial_exit_first_pct is not None:
            self.partial_exit_first_pct = partial_exit_first_pct
        elif first_pct is not None:
            self.partial_exit_first_pct = first_pct
        else:
            self.partial_exit_first_pct = 50.0

        if partial_exit_first_time is not None:
            self.partial_exit_first_time = partial_exit_first_time
        elif first_time is not None:
            self.partial_exit_first_time = first_time
        else:
            self.partial_exit_first_time = "09:15"

        if partial_exit_second_pct is not None:
            self.partial_exit_second_pct = partial_exit_second_pct
        elif second_pct is not None:
            self.partial_exit_second_pct = second_pct
        else:
            self.partial_exit_second_pct = 50.0

        if partial_exit_second_time is not None:
            self.partial_exit_second_time = partial_exit_second_time
        elif second_time is not None:
            self.partial_exit_second_time = second_time
        else:
            self.partial_exit_second_time = "15:00"

    @property
    def enabled(self) -> bool:
        return self.partial_exit_enabled

    @property
    def first_pct(self) -> float:
        return self.partial_exit_first_pct

    @property
    def first_time(self) -> str:
        return self.partial_exit_first_time

    @property
    def second_pct(self) -> float:
        return self.partial_exit_second_pct

    @property
    def second_time(self) -> str:
        return self.partial_exit_second_time

    def to_dict(self) -> dict:
        return {
            "partial_exit_enabled": self.partial_exit_enabled,
            "partial_exit_first_pct": self.partial_exit_first_pct,
            "partial_exit_first_time": self.partial_exit_first_time,
            "partial_exit_second_pct": self.partial_exit_second_pct,
            "partial_exit_second_time": self.partial_exit_second_time,
        }

    def validate(self) -> None:
        if not self.partial_exit_enabled:
            return
        if not (0.0 < self.partial_exit_first_pct <= 100.0):
            raise ValueError(f"partial_exit_first_pct must be between 0 and 100, got {self.partial_exit_first_pct}")
        if not (0.0 < self.partial_exit_second_pct <= 100.0):
            raise ValueError(f"partial_exit_second_pct must be between 0 and 100, got {self.partial_exit_second_pct}")
        if not math.isclose(self.partial_exit_first_pct + self.partial_exit_second_pct, 100.0, rel_tol=1e-5):
            raise ValueError(
                f"partial_exit percentages must sum to 100%, got {self.partial_exit_first_pct + self.partial_exit_second_pct}%"
            )
        if self.partial_exit_first_time >= self.partial_exit_second_time:
            raise ValueError(
                f"partial_exit_first_time ({self.partial_exit_first_time}) must be strictly earlier than partial_exit_second_time ({self.partial_exit_second_time})"
            )


@dataclass
class BacktestConfig:
    """Full backtest configuration — the single object passed through the pipeline."""
    strategy: StrategyParams = field(default_factory=StrategyParams)
    execution: ExecutionParams = field(default_factory=ExecutionParams)
    sizing: SizingParams = field(default_factory=SizingParams)
    fees: FeeParams = field(default_factory=FeeParams)
    partial_exit: PartialExitParams = field(default_factory=PartialExitParams)

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
        if "partial_exit" in d:
            pe = d["partial_exit"]
            cfg.partial_exit = PartialExitParams(
                partial_exit_enabled=pe.get("partial_exit_enabled", pe.get("enabled", False)),
                partial_exit_first_pct=float(pe.get("partial_exit_first_pct", pe.get("first_pct", 50.0))),
                partial_exit_first_time=str(pe.get("partial_exit_first_time", pe.get("first_time", "09:15"))),
                partial_exit_second_pct=float(pe.get("partial_exit_second_pct", pe.get("second_pct", 50.0))),
                partial_exit_second_time=str(pe.get("partial_exit_second_time", pe.get("second_time", "15:00"))),
            )
        elif "partial_exit_enabled" in d:
            cfg.partial_exit = PartialExitParams(
                partial_exit_enabled=bool(d.get("partial_exit_enabled", False)),
                partial_exit_first_pct=float(d.get("partial_exit_first_pct", 50.0)),
                partial_exit_first_time=str(d.get("partial_exit_first_time", "09:15")),
                partial_exit_second_pct=float(d.get("partial_exit_second_pct", 50.0)),
                partial_exit_second_time=str(d.get("partial_exit_second_time", "15:00")),
            )
        elif "execution" in d and ("partial_exit_enabled" in d["execution"] or "enabled" in d["execution"]):
            ex = d["execution"]
            cfg.partial_exit = PartialExitParams(
                partial_exit_enabled=bool(ex.get("partial_exit_enabled", ex.get("enabled", False))),
                partial_exit_first_pct=float(ex.get("partial_exit_first_pct", ex.get("first_pct", 50.0))),
                partial_exit_first_time=str(ex.get("partial_exit_first_time", ex.get("first_time", "09:15"))),
                partial_exit_second_pct=float(ex.get("partial_exit_second_pct", ex.get("second_pct", 50.0))),
                partial_exit_second_time=str(ex.get("partial_exit_second_time", ex.get("second_time", "15:00"))),
            )
        cfg.start_date = d.get("start_date")
        cfg.end_date = d.get("end_date")
        cfg.symbols = d.get("symbols", [])
        return cfg
