# core/data_provider.py
# Назначение: единый поставщик OHLCV-данных для проекта.
# Отвечает за: загрузку свечей с Binance, сохранение актуального CSV, архивирование старых файлов и очистку архива по сроку.
# Связано с: core/auto_chart.py, tests/AD/scripts/*, tools/*, Telegram/LM pipeline.

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)

# Retry конфигурация для Binance API
_BINANCE_MAX_RETRIES = 3
_BINANCE_RETRY_DELAYS = [1.0, 3.0, 7.0]  # секунд


@dataclass
class OhlcvRequest:
    symbol: str
    timeframe: str = "1h"
    limit: int = 500
    market_type: str = "future"  # "future" | "spot"
    force_refresh: bool = False


class OhlcvDataProvider:
    """
    Единый слой данных:
    - текущий CSV хранится в data/ohlcv/current/
    - старые версии уходят в data/ohlcv/archive/
    """

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        archive_days: int = 180,
        max_archive_files_per_symbol_tf: int = 20,
    ) -> None:
        self.project_root = (base_dir or Path(__file__).resolve().parents[1])
        self.ohlcv_root = self.project_root / "data" / "ohlcv"
        self.current_dir = self.ohlcv_root / "current"
        self.archive_dir = self.ohlcv_root / "archive"

        self.archive_days = archive_days
        self.max_archive_files_per_symbol_tf = max_archive_files_per_symbol_tf

        self.current_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return symbol.replace("/", "").upper().strip()

    @staticmethod
    def _normalize_tf(timeframe: str) -> str:
        return timeframe.strip().lower()

    def _base_name(self, symbol: str, timeframe: str) -> str:
        return f"{self._normalize_symbol(symbol)}_{self._normalize_tf(timeframe)}.csv"

    def current_path(self, symbol: str, timeframe: str) -> Path:
        return self.current_dir / self._base_name(symbol, timeframe)

    def archive_subdir(self, symbol: str, timeframe: str) -> Path:
        p = self.archive_dir / self._normalize_symbol(symbol) / self._normalize_tf(timeframe)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def fetch_from_binance(self, req: OhlcvRequest) -> pd.DataFrame:
        tf = self._normalize_tf(req.timeframe)
        market_type = req.market_type if req.market_type in ("future", "spot") else "future"

        last_exc: Exception | None = None
        for attempt in range(1, _BINANCE_MAX_RETRIES + 1):
            try:
                exchange = ccxt.binance({
                    "options": {"defaultType": market_type},
                    "enableRateLimit": True,
                })
                bars = exchange.fetch_ohlcv(req.symbol, tf, limit=req.limit)

                df = pd.DataFrame(
                    bars,
                    columns=["timestamp", "open", "high", "low", "close", "volume"],
                )

                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                df["time"] = df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")

                # Канонический порядок для всех downstream-скриптов
                df = df[["time", "timestamp", "open", "high", "low", "close", "volume"]]
                return df

            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RateLimitExceeded) as e:
                last_exc = e
                logger.warning(
                    "Binance attempt %d/%d failed for %s %s: %s",
                    attempt, _BINANCE_MAX_RETRIES, req.symbol, tf, e,
                )
                if attempt < _BINANCE_MAX_RETRIES:
                    delay = _BINANCE_RETRY_DELAYS[min(attempt - 1, len(_BINANCE_RETRY_DELAYS) - 1)]
                    import time
                    time.sleep(delay)
                continue

            except Exception as e:
                last_exc = e
                logger.error("Binance fatal error for %s %s: %s", req.symbol, tf, e)
                break

        raise ConnectionError(
            f"Binance API unavailable after {_BINANCE_MAX_RETRIES} attempts "
            f"for {req.symbol} {tf}: {last_exc}"
        ) from last_exc

    def _ensure_numeric(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        for col in ["open", "high", "low", "close", "volume"]:
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")
        if "timestamp" in out.columns:
            out["timestamp"] = pd.to_datetime(out["timestamp"], errors="coerce", utc=True)
        if "time" in out.columns:
            out["time"] = pd.to_datetime(out["time"], errors="coerce", utc=True).dt.strftime("%Y-%m-%d %H:%M:%S")
        return out.dropna(subset=[c for c in ["open", "high", "low", "close", "volume"] if c in out.columns]).reset_index(drop=True)

    def read_current_csv(self, symbol: str, timeframe: str) -> pd.DataFrame:
        path = self.current_path(symbol, timeframe)
        if not path.exists():
            raise FileNotFoundError(f"CSV not found: {path}")
        df = pd.read_csv(path)
        return self._ensure_numeric(df)

    def archive_existing_current(self, symbol: str, timeframe: str) -> Optional[Path]:
        current = self.current_path(symbol, timeframe)
        if not current.exists():
            return None

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_name = f"{self._normalize_symbol(symbol)}_{self._normalize_tf(timeframe)}_{ts}.csv"
        target = self.archive_subdir(symbol, timeframe) / archive_name
        current.replace(target)
        return target

    def save_current_csv(self, df: pd.DataFrame, symbol: str, timeframe: str) -> Path:
        df2 = self._ensure_numeric(df)

        # перед сохранением старый current переносим в archive
        self.archive_existing_current(symbol, timeframe)

        path = self.current_path(symbol, timeframe)
        df2.to_csv(path, index=False, encoding="utf-8")
        return path

    def cleanup_archive(self) -> int:
        """
        Удаляет архивные файлы старше archive_days и лишние файлы сверх лимита.
        Возвращает количество удалённых файлов.
        """
        removed = 0
        now = datetime.now(timezone.utc)

        # 1) чистим по возрасту
        for csv_path in self.archive_dir.rglob("*.csv"):
            try:
                mtime = datetime.fromtimestamp(csv_path.stat().st_mtime, tz=timezone.utc)
                if now - mtime > timedelta(days=self.archive_days):
                    csv_path.unlink(missing_ok=True)
                    removed += 1
            except Exception:
                continue

        # 2) чистим по количеству на пару symbol/timeframe
        for symbol_dir in self.archive_dir.iterdir():
            if not symbol_dir.is_dir():
                continue
            for tf_dir in symbol_dir.iterdir():
                if not tf_dir.is_dir():
                    continue

                files = sorted(
                    [p for p in tf_dir.glob("*.csv") if p.is_file()],
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                for extra in files[self.max_archive_files_per_symbol_tf:]:
                    try:
                        extra.unlink(missing_ok=True)
                        removed += 1
                    except Exception:
                        continue

        return removed

    def _tf_ttl_seconds(self, timeframe: str) -> float:
        """TTL кэша по таймфрейму: одна свеча. M15=15мин, 1H=1час и т.д."""
        tf = timeframe.strip().lower()
        ttl_map = {
            "15m": 15 * 60,
            "1h": 60 * 60,
            "4h": 4 * 60 * 60,
            "1d": 24 * 60 * 60,
            "1w": 7 * 24 * 60 * 60,
        }
        return ttl_map.get(tf, 60 * 60)  # default 1h

    def ensure_ohlcv(self, req: OhlcvRequest) -> tuple[pd.DataFrame, Path]:
        path = self.current_path(req.symbol, req.timeframe)

        if path.exists() and not req.force_refresh:
            # TTL: если файл старее одной свечи данного ТФ — обновить
            file_age = (datetime.now(timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)).total_seconds()
            ttl = self._tf_ttl_seconds(req.timeframe)
            if file_age < ttl:
                return self.read_current_csv(req.symbol, req.timeframe), path
            logger.info(f"Cache TTL expired for {req.symbol} {req.timeframe} (age={file_age:.0f}s, ttl={ttl:.0f}s) — refreshing")

        df = self.fetch_from_binance(req)
        path = self.save_current_csv(df, req.symbol, req.timeframe)
        self.cleanup_archive()
        return df, path

    def refresh_many(
        self,
        symbols: list[str],
        timeframes: list[str],
        limit: int = 500,
        market_type: str = "future",
        force_refresh: bool = True,
    ) -> list[Path]:
        paths: list[Path] = []
        for s in symbols:
            for tf in timeframes:
                req = OhlcvRequest(
                    symbol=s,
                    timeframe=tf,
                    limit=limit,
                    market_type=market_type,
                    force_refresh=force_refresh,
                )
                _, p = self.ensure_ohlcv(req)
                paths.append(p)
        return paths

    def list_current_csv(self, symbol: Optional[str] = None) -> list[Path]:
        if symbol is None:
            return sorted(self.current_dir.glob("*.csv"))
        prefix = self._normalize_symbol(symbol) + "_"
        return sorted([p for p in self.current_dir.glob("*.csv") if p.name.startswith(prefix)])

    def list_archive_csv(self, symbol: Optional[str] = None) -> list[Path]:
        if symbol is None:
            return sorted(self.archive_dir.rglob("*.csv"))
        return sorted(self.archive_dir.rglob(f"{self._normalize_symbol(symbol)}_*.csv"))