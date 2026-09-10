"""
Signal engine: pure, stateless function that evaluates strategy conditions
for a single (symbol, day) combination.

ZERO look-ahead guarantee:
  - Only receives: the current day's OHLCV row + precomputed 20-day avg volume
  - Does NOT access any future rows
  - The caller is responsible for computing avg_volume from prior rows only
"""
from dataclasses import dataclass
from typing import Optional
import math

from app.strategy.params import StrategyParams


@dataclass
class SignalResult:
    # Raw computed values
    range_pct: float
    body_pct: float
    close_loc_pct: float
    volume: float
    avg_volume_20d: float
    volume_multiple: float
    prev_close_distance_pct: float = 0.0

    # Per-condition pass flags
    range_pass: bool = False
    body_pass: bool = False
    close_loc_pass: bool = False
    volume_pass: bool = False
    prev_close_pass: bool = True
    has_sufficient_history: bool = False  # False if fewer than lookback prior days

    # Aggregate signal
    signal: bool = False  # True only if all conditions pass and history is sufficient
    score: float = 0.0  # Breakout strength score (how strongly conditions were exceeded)

    def to_dict(self) -> dict:
        return {
            "range_pct": round(float(self.range_pct), 4),
            "body_pct": round(float(self.body_pct), 4),
            "close_loc_pct": round(float(self.close_loc_pct), 4),
            "volume": float(self.volume),
            "avg_volume_20d": round(float(self.avg_volume_20d), 2),
            "volume_multiple": round(float(self.volume_multiple), 4),
            "prev_close_distance_pct": round(float(self.prev_close_distance_pct), 4),
            "range_pass": bool(self.range_pass),
            "body_pass": bool(self.body_pass),
            "close_loc_pass": bool(self.close_loc_pass),
            "volume_pass": bool(self.volume_pass),
            "prev_close_pass": bool(self.prev_close_pass),
            "has_sufficient_history": bool(self.has_sufficient_history),
            "signal": bool(self.signal),
            "score": round(float(self.score), 4),
        }


def compute_signal(
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    avg_volume_20d: float,       # precomputed mean of PRIOR lookback days (today excluded)
    has_sufficient_history: bool,
    params: StrategyParams,
    prev_close: Optional[float] = None,
) -> SignalResult:
    """
    Evaluate all strategy conditions for a single day.

    Parameters
    ----------
    open_, high, low, close : float  OHLCV for the signal day session
    volume : float                   Today's session volume
    avg_volume_20d : float           20-day (or lookback) trailing average volume
                                     computed ONLY from prior completed days
    has_sufficient_history : bool    True when >= lookback prior rows available
    params : StrategyParams          All thresholds (no hardcoded values)
    prev_close : Optional[float]     Previous day's close for distance filter

    Returns
    -------
    SignalResult with all debug fields and final signal flag
    """
    # ── 1. Range %: (High - Low) / Low × 100 ──────────────────────────────
    if low <= 0:
        range_pct = 0.0
    else:
        range_pct = (high - low) / low * 100.0

    # ── 2. Bullish Body %: (Close - Open) / Open × 100 ────────────────────
    if open_ <= 0:
        body_pct = 0.0
    else:
        body_pct = (close - open_) / open_ * 100.0

    # ── 3. Close Location %: (Close - Low) / (High - Low) × 100 ──────────
    hl_range = high - low
    if hl_range <= 0:
        close_loc_pct = 0.0
    else:
        close_loc_pct = (close - low) / hl_range * 100.0

    # ── 4. Volume multiple ─────────────────────────────────────────────────
    if avg_volume_20d > 0:
        volume_multiple = volume / avg_volume_20d
    else:
        volume_multiple = 0.0

    # ── Condition checks ───────────────────────────────────────────────────
    range_pass = range_pct >= params.min_range_pct or math.isclose(range_pct, params.min_range_pct, abs_tol=1e-9)
    max_range = getattr(params, "max_range_pct", 0.0) or 0.0
    if max_range > 0:
        range_pass = range_pass and (range_pct <= max_range or math.isclose(range_pct, max_range, abs_tol=1e-9))

    body_pass = body_pct > params.min_body_pct and not math.isclose(body_pct, params.min_body_pct, abs_tol=1e-9)
    max_body = getattr(params, "max_body_pct", 0.0) or 0.0
    if max_body > 0:
        body_pass = body_pass and (body_pct <= max_body or math.isclose(body_pct, max_body, abs_tol=1e-9))

    close_loc_pass = close_loc_pct >= params.min_close_loc_pct or math.isclose(close_loc_pct, params.min_close_loc_pct, abs_tol=1e-9)
    max_close_loc = getattr(params, "max_close_loc_pct", 0.0) or 0.0
    if max_close_loc > 0:
        close_loc_pass = close_loc_pass and (close_loc_pct <= max_close_loc or math.isclose(close_loc_pct, max_close_loc, abs_tol=1e-9))
    volume_pass = volume_multiple > params.volume_multiplier and not math.isclose(volume_multiple, params.volume_multiplier, abs_tol=1e-9) and has_sufficient_history

    # Previous day close distance filter (flexible)
    prev_close_filter = getattr(params, "prev_close_filter_enabled", False)
    max_prev_close = getattr(params, "max_prev_close_pct", 20.0)
    min_prev_close = getattr(params, "min_prev_close_pct", None)
    filter_mode = getattr(params, "prev_close_filter_mode", "less_than_or_equal")
    prev_close_distance_pct = 0.0
    prev_close_pass = True

    if prev_close_filter and prev_close is not None and prev_close > 0:
        prev_close_distance_pct = (close - prev_close) / prev_close * 100.0
        
        pass_max = True
        if max_prev_close is not None:
            pass_max = prev_close_distance_pct <= max_prev_close or math.isclose(prev_close_distance_pct, max_prev_close, abs_tol=1e-9)
            
        pass_min = True
        if min_prev_close is not None:
            pass_min = prev_close_distance_pct >= min_prev_close or math.isclose(prev_close_distance_pct, min_prev_close, abs_tol=1e-9)

        if filter_mode in ("greater_than_or_equal", "min"):
            prev_close_pass = pass_min
        elif filter_mode in ("between", "range"):
            prev_close_pass = pass_min and pass_max
        else:  # "less_than_or_equal" / default
            prev_close_pass = pass_max

    all_pass = (
        has_sufficient_history
        and range_pass
        and body_pass
        and close_loc_pass
        and volume_pass
        and prev_close_pass
    )

    # Breakout strength score: measures how strongly conditions were exceeded
    vol_ratio = (volume_multiple / params.volume_multiplier) if params.volume_multiplier > 0 else 1.0
    body_ratio = (body_pct / params.min_body_pct) if params.min_body_pct > 0 else 1.0
    range_ratio = (range_pct / params.min_range_pct) if params.min_range_pct > 0 else 1.0
    close_ratio = (close_loc_pct / params.min_close_loc_pct) if params.min_close_loc_pct > 0 else 1.0
    score = vol_ratio + body_ratio + range_ratio + close_ratio

    return SignalResult(
        range_pct=range_pct,
        body_pct=body_pct,
        close_loc_pct=close_loc_pct,
        volume=volume,
        avg_volume_20d=avg_volume_20d,
        volume_multiple=volume_multiple,
        prev_close_distance_pct=prev_close_distance_pct,
        range_pass=range_pass,
        body_pass=body_pass,
        close_loc_pass=close_loc_pass,
        volume_pass=volume_pass,
        prev_close_pass=prev_close_pass,
        has_sufficient_history=has_sufficient_history,
        signal=all_pass,
        score=score if all_pass else 0.0,
    )


def compute_signals_for_symbol(df_symbol, params: StrategyParams) -> list[dict]:
    """
    Compute signal results for every row of a symbol's daily DataFrame.
    Returns a list of dicts (one per row) with date + signal fields.

    NO look-ahead: avg_volume for row i is computed from rows [i-lookback .. i-1].
    """
    rows = df_symbol.to_dict("records")
    results = []
    lb = params.volume_lookback

    for i, row in enumerate(rows):
        # Compute trailing average volume from prior rows only
        prior_rows = rows[max(0, i - lb) : i]
        has_sufficient = len(prior_rows) >= lb
        if has_sufficient:
            avg_vol = sum(r["volume"] for r in prior_rows) / lb
        elif prior_rows:
            avg_vol = sum(r["volume"] for r in prior_rows) / len(prior_rows)
        else:
            avg_vol = 0.0

        prev_close = None
        if i > 0:
            prev_row = rows[i - 1]
            ref_norm = str(getattr(params, "prev_close_ref", "candle_1525")).lower().strip()
            if "1525" in ref_norm or "3:25" in ref_norm or "325" in ref_norm:
                prev_close = prev_row.get("candle_1525_close") or prev_row.get("daily_close") or prev_row.get("close")
            elif "1520" in ref_norm or "3:20" in ref_norm or "320" in ref_norm:
                prev_close = prev_row.get("candle_1520_close") or prev_row.get("close")
            elif "daily" in ref_norm or "eod" in ref_norm:
                prev_close = prev_row.get("daily_close") or prev_row.get("close")
            else:
                prev_close = prev_row.get("candle_1525_close") or prev_row.get("daily_close") or prev_row.get("close")
        sig = compute_signal(
            open_=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
            avg_volume_20d=avg_vol,
            has_sufficient_history=has_sufficient,
            params=params,
            prev_close=prev_close,
        )
        entry = {"date": str(row["date"]), **sig.to_dict()}
        # Add raw OHLCV for debugger
        entry["open"] = row["open"]
        entry["high"] = row["high"]
        entry["low"] = row["low"]
        entry["close"] = row["close"]
        results.append(entry)

    return results
