"""
Data loader: reads {SYMBOL}_daily.parquet files from D:/datas/.
Uses PyArrow for fast I/O and returns DataFrames indexed by date.
Results are cached in-process to avoid repeated disk reads.
"""
import os
import glob
import logging
from datetime import date
from typing import Optional
import pyarrow.parquet as pq
import pyarrow as pa
import pandas as pd
import numpy as np

from dotenv import load_dotenv, find_dotenv

logger = logging.getLogger(__name__)

# Automatically load .env (checks current directory, backend directory, and ancestors)
load_dotenv(find_dotenv(usecwd=True))
_backend_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
if os.path.exists(_backend_env):
    load_dotenv(_backend_env)

DATA_DIR = os.environ.get("BACKTESTER_DATA_DIR", "").strip("\"'")
if not DATA_DIR or not os.path.exists(DATA_DIR):
    if os.path.exists(r"D:\datas"):
        DATA_DIR = r"D:\datas"
    elif os.path.exists(r"E:\datas"):
        DATA_DIR = r"E:\datas"
    else:
        DATA_DIR = r"D:\datas"

logger.info(f"Loaded DATA_DIR: '{DATA_DIR}' (exists: {os.path.exists(DATA_DIR)})")
_CACHE: dict[str, pd.DataFrame] = {}  # symbol → cleaned daily DataFrame


def _parquet_path(symbol: str, timeframe: str = "5min") -> Optional[str]:
    path = os.path.join(DATA_DIR, f"{symbol}_{timeframe}.parquet")
    if os.path.exists(path):
        return path
    # try case-insensitive fallback
    pattern = os.path.join(DATA_DIR, f"{symbol.upper()}_{timeframe}.parquet")
    if os.path.exists(pattern):
        return pattern
    return None


def _clean_and_aggregate_5min(df: pd.DataFrame, symbol: str, signal_time: str = "15:20") -> pd.DataFrame:
    """Normalize, filter to signal_time (default 15:20), and aggregate 5-min data to daily."""
    df = df.copy()
    
    if "datetime" not in df.columns and "Date" in df.columns:
        df.rename(columns={"Date": "datetime"}, inplace=True)
    if "datetime" not in df.columns:
        raise ValueError(f"[{symbol}] No datetime column found")

    # Standardize OHLCV column names (lower-case)
    rename = {}
    for col in df.columns:
        lc = col.lower()
        if lc in ("open", "high", "low", "close", "volume"):
            rename[col] = lc
    df.rename(columns=rename, inplace=True)

    # Convert to IST and extract date and time
    dt_ist = pd.to_datetime(df["datetime"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["date"] = dt_ist.dt.date
    df["time"] = dt_ist.dt.time

    # Filter to market hours strictly known at signal check time:
    # For 15:20 (default): Cutoff is completed 15:15 candle (covers 15:15–15:20).
    # The 15:20 candle covers 15:20–15:25 and is excluded to allow the 5-min order placement window.
    # For 15:25: Cutoff is completed 15:20 candle (covers 15:20–15:25).
    if signal_time == "15:20":
        cutoff_time = pd.to_datetime("15:15:00").time()
    else:
        cutoff_time = pd.to_datetime("15:20:00").time()
    df = df[df["time"] <= cutoff_time]

    # Drop missing
    df.dropna(subset=["open", "high", "low", "close", "volume"], inplace=True)
    df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]

    # Map target exit times to the candle whose close represents the price at that exact time.
    # E.g. Candle 09:15 covers 09:15-09:20 and completes at 09:20:00.
    # Candle 09:25 covers 09:25-09:30 and completes at 09:30:00.
    # Candle 14:55 covers 14:55-15:00 and completes at 15:00:00.
    from datetime import time as dtime
    candle_time_map = {
        dtime(9, 15): "price_0920",
        dtime(9, 20): "price_0925",
        dtime(9, 25): "price_0930",
        dtime(9, 40): "price_0945",
        dtime(9, 55): "price_1000",
        dtime(10, 55): "price_1100",
        dtime(11, 55): "price_1200",
        dtime(12, 55): "price_1300",
        dtime(13, 55): "price_1400",
        dtime(14, 55): "price_1500",
        dtime(15, 10): "price_1515",
        dtime(15, 15): "price_1520",
    }

    sub = df[df["time"].isin(candle_time_map.keys())].drop_duplicates(["date", "time"], keep="last")
    if not sub.empty:
        piv = sub.pivot(index="date", columns="time", values="close").reset_index()
        piv.rename(columns=candle_time_map, inplace=True)
    else:
        piv = pd.DataFrame({"date": df["date"].unique()})

    # Aggregate by date
    daily = df.groupby("date").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum"
    }).reset_index()

    daily = daily.merge(piv, on="date", how="left")

    # Chronologically forward-fill missing intraday prices:
    # 09:15 open -> 09:20 -> 09:25 -> 09:30 -> ... -> 15:00 -> 15:20 -> close
    time_cols = [
        "price_0920", "price_0925", "price_0930", "price_0945", "price_1000",
        "price_1100", "price_1200", "price_1300", "price_1400", "price_1500",
        "price_1515", "price_1520"
    ]
    prev_col = "open"
    for col in time_cols:
        if col not in daily.columns:
            daily[col] = float("nan")
        daily[col] = daily[col].fillna(daily[prev_col])
        prev_col = col

    # Backward fallback for 15:00 / 15:20 if ever NaN
    daily["price_1500"] = daily["price_1500"].fillna(daily["close"])
    daily["price_1520"] = daily["price_1520"].fillna(daily["close"])

    # Sort chronologically
    daily.sort_values("date", inplace=True)
    daily.reset_index(drop=True, inplace=True)

    return daily


def load_symbol(symbol: str, signal_time: str = "15:20", force_reload: bool = False) -> Optional[pd.DataFrame]:
    """
    Load and cache daily OHLCV data for symbol.
    Returns None if parquet file not found.
    """
    symbol = symbol.strip().upper()
    cache_key = f"{symbol}_{signal_time}"
    if not force_reload and cache_key in _CACHE:
        return _CACHE[cache_key]

    path = _parquet_path(symbol)
    if path is None:
        logger.warning(f"No daily parquet found for {symbol}")
        return None

    try:
        table = pq.read_table(path, columns=["datetime", "open", "high", "low", "close", "volume"])
        df = table.to_pandas()
        df = _clean_and_aggregate_5min(df, symbol, signal_time=signal_time)
        _CACHE[cache_key] = df
        return df
    except Exception as e:
        logger.error(f"Error loading {symbol}: {e}")
        return None


def load_symbols_bulk(symbols: list[str], workers: int = 8, signal_time: str = "15:20", progress_callback=None) -> dict[str, pd.DataFrame]:
    """Load multiple symbols in parallel using ThreadPoolExecutor."""
    from concurrent.futures import ThreadPoolExecutor
    import threading
    result: dict[str, pd.DataFrame] = {}
    lock = threading.Lock()
    count = 0
    total = len(symbols)

    def _load(sym: str):
        nonlocal count
        df = load_symbol(sym, signal_time=signal_time)
        with lock:
            if df is not None:
                result[sym] = df
            count += 1
            if progress_callback and (count % 100 == 0 or count == total):
                progress_callback(f"Loading data: {count}/{total} symbols ({int(count/total*100)}%)...")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_load, symbols))

    return result


def get_available_symbols(timeframe: str = "daily") -> list[str]:
    """Scan DATA_DIR and return list of all symbols with daily data."""
    pattern = os.path.join(DATA_DIR, f"*_{timeframe}.parquet")
    files = glob.glob(pattern)
    symbols = []
    for f in files:
        basename = os.path.basename(f)
        sym = basename.replace(f"_{timeframe}.parquet", "")
        symbols.append(sym)
    return sorted(symbols)


def get_symbol_date_range(symbol: str, signal_time: str = "15:20") -> tuple[Optional[date], Optional[date]]:
    df = load_symbol(symbol, signal_time=signal_time)
    if df is None or df.empty:
        return None, None
    return df["date"].iloc[0], df["date"].iloc[-1]


def clear_cache() -> None:
    global _CACHE
    _CACHE.clear()
    logger.info("Data cache cleared")
