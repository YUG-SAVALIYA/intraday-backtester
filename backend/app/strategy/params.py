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
    max_range_pct: float = 0.0         # Range % <= this (0 = disabled / no upper limit)
    min_body_pct: float = 5.0          # Bullish body % > this
    max_body_pct: float = 0.0          # Bullish body % <= this (0 = disabled / no upper limit)
    min_close_loc_pct: float = 90.0    # Close location % >= this
    max_close_loc_pct: float = 0.0     # Close location % <= this (0 = disabled / no upper limit)

    # Volume filter
    volume_lookback: int = 20          # Number of prior trading days for avg volume
    volume_multiplier: float = 2.5     # Optimized: 2.5x volume spike

    # Previous day close distance filter (flexible reference & thresholds)
    prev_close_filter_enabled: bool = False
    prev_close_ref: str = "candle_1525"        # "candle_1525" (last day 3:25 candle close), "candle_1520" (3:20 candle close / 3:25 PM), "daily_close" (EOD close)
    prev_close_filter_mode: str = "less_than_or_equal"  # "less_than_or_equal", "greater_than_or_equal", "between"
    max_prev_close_pct: float = 20.0           # Max % distance from prev ref close to today 3:20 close
    min_prev_close_pct: Optional[float] = None # Min % distance (optional, e.g. -100.0 or 0.0)


@dataclass
class ExecutionParams:
    """Trade execution configuration."""
    # Entry: price to use on signal day (default 15:20 close)
    entry_price_field: str = "close"   # "close", "open", "high", "low"
    entry_time: str = "15:20"          # Signal check and entry time ("15:20" or "15:25")
    entry_slippage_pct: float = 0.05   # % slippage on entry (0.05 = 5 bps)

    # Exit: next trading day open or selected exit time
    exit_price_field: str = "open"     # field from next day row ("open", "price_0920", etc.)
    exit_time: str = "09:15"           # Exit time for single/full exit (e.g. "09:15", "09:20", "09:30", "15:00")
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


def normalize_time_str(t: str) -> str:
    """Normalize a time string to standard 24-hour 'HH:MM' format."""
    if not t:
        return "09:15"
    s = str(t).strip().lower()
    if s in ("open", "market_open"):
        return "09:15"
    if s in ("close", "market_close"):
        return "15:25"
    if s in ("3pm", "3:00pm", "3:00 pm", "1500"):
        return "15:00"
    if ":" in s:
        parts = s.split(":")
        try:
            hh = int(parts[0])
            mm_str = parts[1][:2]
            mm = int(mm_str)
            if "pm" in s and hh < 12:
                hh += 12
            return f"{hh:02d}:{mm:02d}"
        except Exception:
            return s
    if len(s) == 4 and s.isdigit():
        return f"{s[:2]}:{s[2:]}"
    return s


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
            self.partial_exit_enabled = bool(partial_exit_enabled)
        elif enabled is not None:
            self.partial_exit_enabled = bool(enabled)
        else:
            self.partial_exit_enabled = False

        if partial_exit_first_pct is not None:
            self.partial_exit_first_pct = float(partial_exit_first_pct)
        elif first_pct is not None:
            self.partial_exit_first_pct = float(first_pct)
        else:
            self.partial_exit_first_pct = 50.0

        raw_first_time = "09:15"
        if partial_exit_first_time is not None:
            raw_first_time = str(partial_exit_first_time)
        elif first_time is not None:
            raw_first_time = str(first_time)
        self.partial_exit_first_time = normalize_time_str(raw_first_time)

        if partial_exit_second_pct is not None:
            self.partial_exit_second_pct = float(partial_exit_second_pct)
        elif second_pct is not None:
            self.partial_exit_second_pct = float(second_pct)
        else:
            self.partial_exit_second_pct = 50.0

        raw_second_time = "15:00"
        if partial_exit_second_time is not None:
            raw_second_time = str(partial_exit_second_time)
        elif second_time is not None:
            raw_second_time = str(second_time)
        self.partial_exit_second_time = normalize_time_str(raw_second_time)

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

    # Signal check and entry time ("15:20" or "15:25")
    signal_time: str = "15:20"

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
            strat_d = dict(d["strategy"])
            # Handle aliases for max body / nody range
            if "max_body_range" in strat_d and "max_body_pct" not in strat_d:
                strat_d["max_body_pct"] = strat_d.pop("max_body_range")
            if "max_nody_range" in strat_d and "max_body_pct" not in strat_d:
                strat_d["max_body_pct"] = strat_d.pop("max_nody_range")
            if "max_body" in strat_d and "max_body_pct" not in strat_d:
                strat_d["max_body_pct"] = strat_d.pop("max_body")
            if "max_range" in strat_d and "max_range_pct" not in strat_d:
                strat_d["max_range_pct"] = strat_d.pop("max_range")
            if "max_close_location" in strat_d and "max_close_loc_pct" not in strat_d:
                strat_d["max_close_loc_pct"] = strat_d.pop("max_close_location")
            if "max_close_loc" in strat_d and "max_close_loc_pct" not in strat_d:
                strat_d["max_close_loc_pct"] = strat_d.pop("max_close_loc")
            if "max_close_loc_pct" in strat_d and strat_d["max_close_loc_pct"] is not None:
                strat_d["max_close_loc_pct"] = float(strat_d["max_close_loc_pct"])
            # Prev close filter aliases & flexibility
            if "prev_close_reference" in strat_d and "prev_close_ref" not in strat_d:
                strat_d["prev_close_ref"] = strat_d.pop("prev_close_reference")
            if "prev_close_candle" in strat_d and "prev_close_ref" not in strat_d:
                strat_d["prev_close_ref"] = strat_d.pop("prev_close_candle")
            if "prev_close_mode" in strat_d and "prev_close_filter_mode" not in strat_d:
                strat_d["prev_close_filter_mode"] = strat_d.pop("prev_close_mode")
            if "min_prev_close" in strat_d and "min_prev_close_pct" not in strat_d:
                strat_d["min_prev_close_pct"] = strat_d.pop("min_prev_close")
            if "max_prev_close" in strat_d and "max_prev_close_pct" not in strat_d:
                strat_d["max_prev_close_pct"] = strat_d.pop("max_prev_close")
            if "min_prev_close_pct" in strat_d and strat_d["min_prev_close_pct"] is not None:
                strat_d["min_prev_close_pct"] = float(strat_d["min_prev_close_pct"])
            if "max_prev_close_pct" in strat_d and strat_d["max_prev_close_pct"] is not None:
                strat_d["max_prev_close_pct"] = float(strat_d["max_prev_close_pct"])
            known = {
                "min_range_pct", "max_range_pct", "min_body_pct", "max_body_pct",
                "min_close_loc_pct", "max_close_loc_pct", "volume_lookback", "volume_multiplier",
                "prev_close_filter_enabled", "prev_close_ref", "prev_close_filter_mode",
                "max_prev_close_pct", "min_prev_close_pct"
            }
            clean_strat = {k: v for k, v in strat_d.items() if k in known}
            cfg.strategy = StrategyParams(**clean_strat)
        if "execution" in d:
            cfg.execution = ExecutionParams(**d["execution"])
        elif "exit_time" in d:
            cfg.execution.exit_time = str(d["exit_time"])
        if "signal_time" in d:
            cfg.signal_time = str(d["signal_time"])
        elif "execution" in d and "entry_time" in d["execution"]:
            cfg.signal_time = str(d["execution"]["entry_time"])
        if hasattr(cfg.execution, "entry_time") and cfg.signal_time:
            cfg.execution.entry_time = cfg.signal_time
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
