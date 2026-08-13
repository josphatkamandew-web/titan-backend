"""
DataManager — tries Twelve Data first (cloud, works from the hosted
website), falls back to MT5 only if a local terminal is actually
reachable on this worker. Every result is tagged with which source
actually served it, so the UI's Data Health screen never lies about
where the data came from.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from .base import DataAdapter, DataUnavailableError, FetchResult, validate_ohlcv

logger = logging.getLogger("titan.data_manager")


class DataManager:
    def __init__(self, adapters: List[DataAdapter]):
        if not adapters:
            raise ValueError("DataManager requires at least one adapter.")
        self.adapters = adapters

    @classmethod
    def default(cls) -> "DataManager":
        adapters: List[DataAdapter] = []

        try:
            from .twelvedata_adapter import TwelveDataAdapter
            adapters.append(TwelveDataAdapter())
        except Exception as exc:  # noqa: BLE001 - we want to degrade, not crash import
            logger.warning("Twelve Data adapter unavailable: %s", exc)

        try:
            from .mt5_adapter import MT5Adapter
            adapters.append(MT5Adapter())
        except Exception as exc:  # noqa: BLE001
            logger.info("MT5 adapter unavailable (expected on non-Windows hosts): %s", exc)

        if not adapters:
            raise DataUnavailableError(
                "No data adapters available. Set TWELVE_DATA_API_KEY, or run with a "
                "reachable MT5 terminal."
            )
        return cls(adapters)

    def fetch(self, symbol: str, timeframe: str, bars: int = 1500) -> FetchResult:
        errors = []
        for adapter in self.adapters:
            try:
                result = adapter.fetch_ohlcv(symbol, timeframe, bars)
            except DataUnavailableError as exc:
                errors.append(f"{adapter.name}: {exc}")
                continue

            validation = validate_ohlcv(result.df)
            if not validation["valid"]:
                errors.append(f"{adapter.name}: failed validation {validation['errors']}")
                continue

            result.__dict__["validation"] = validation
            result.__dict__["attempted_sources"] = [a.name for a in self.adapters]
            if adapter is not self.adapters[0]:
                logger.warning(
                    "Primary source failed for %s %s, served by fallback %s",
                    symbol, timeframe, adapter.name,
                )
            return result

        raise DataUnavailableError(
            f"All data sources failed for {symbol} {timeframe}: {errors}"
        )
