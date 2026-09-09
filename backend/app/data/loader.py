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

DATA_DIR = os.environ.get("BACKTESTER_DATA_DIR", r"E:\datas").strip("\"'")
if not os.path.exists(DATA_DIR) and os.path.exists(r"E:\datas"):
    DATA_DIR = r"E:\datas"

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


def _clean_and_aggregate_5min(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Normalize, filter to 15:25, and aggregate 5-min data to daily."""
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

    # Filter to market hours strictly known at 15:25 (up to the completed 15:20 candle).
    # The 15:20 candle covers 15:20–15:25 and is complete at 15:25.
    # The 15:25 candle covers 15:25–15:30 and must be excluded to prevent lookahead.
    cutoff_time = pd.to_datetime("15:20:00").time()
    df = df[df["time"] <= cutoff_time]

    # Drop missing
    df.dropna(subset=["open", "high", "low", "close", "volume"], inplace=True)
    df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]

    # Extract the exact price available at 15:00:00 (close of completed 14:55 candle).
    # Candle 14:55 covers 14:55–15:00 and is complete at 15:00:00.
    # It contains ZERO data from 15:00:01 onwards.
    df_1500 = df[df["time"] <= pd.to_datetime("14:55:00").time()]
    last_1500 = df_1500.groupby("date")["close"].last().rename("price_1500")

    # Aggregate by date
    daily = df.groupby("date").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum"
    }).reset_index()

    daily = daily.merge(last_1500, on="date", how="left")
    daily["price_1500"] = daily["price_1500"].fillna(daily["close"])

    # Sort chronologically
    daily.sort_values("date", inplace=True)
    daily.reset_index(drop=True, inplace=True)

    return daily


def load_symbol(symbol: str, force_reload: bool = False) -> Optional[pd.DataFrame]:
    """
    Load and cache daily OHLCV data for symbol.
    Returns None if parquet file not found.
    """
    symbol = symbol.strip().upper()
    if not force_reload and symbol in _CACHE:
        return _CACHE[symbol]

    path = _parquet_path(symbol)
    if path is None:
        logger.warning(f"No daily parquet found for {symbol}")
        return None

    try:
        table = pq.read_table(path, columns=["datetime", "open", "high", "low", "close", "volume"])
        df = table.to_pandas()
        df = _clean_and_aggregate_5min(df, symbol)
        _CACHE[symbol] = df
        return df
    except Exception as e:
        logger.error(f"Error loading {symbol}: {e}")
        return None


def load_symbols_bulk(symbols: list[str], workers: int = 8, progress_callback=None) -> dict[str, pd.DataFrame]:
    """Load multiple symbols in parallel using ThreadPoolExecutor."""
    from concurrent.futures import ThreadPoolExecutor
    import threading
    result: dict[str, pd.DataFrame] = {}
    lock = threading.Lock()
    count = 0
    total = len(symbols)

    def _load(sym: str):
        nonlocal count
        df = load_symbol(sym)
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


def get_symbol_date_range(symbol: str) -> tuple[Optional[date], Optional[date]]:
    df = load_symbol(symbol)
    if df is None or df.empty:
        return None, None
    return df["date"].iloc[0], df["date"].iloc[-1]


def clear_cache() -> None:
    global _CACHE
    _CACHE.clear()
    logger.info("Data cache cleared")
