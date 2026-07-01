"""
Local JSON cache for flight-status API lookups.

Refactored into a small FlightCache class so callers don't have to pass a
cache_file path around and reload the whole JSON file from disk on every
single row (the original get_flight_live_data() called load_cache() fresh
for *every* passenger row -- O(n) full-file reads for an n-row manifest).
FlightCache loads once and writes through on every update, which is both
simpler and considerably cheaper for larger manifests.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, Optional, Union

logger = logging.getLogger(__name__)


class FlightCache:
    def __init__(self, cache_file: Union[str, Path]):
        self.cache_file = Path(cache_file)
        self._data: Dict[str, dict] = self._load()

    def _load(self) -> Dict[str, dict]:
        if self.cache_file.exists():
            try:
                with self.cache_file.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not read cache file, starting fresh: {e}")
        return {}

    def _save(self) -> None:
        try:
            with self.cache_file.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Could not write to cache file: {e}")

    @staticmethod
    def make_key(flight_number: str, pickup_time_str: str, manifest_date: str) -> str:
        """
        Unique compound key for a shuttle lookup, strictly scoped by date so
        stale data from a previous day's manifest can never be served back.
        """
        if not manifest_date or str(manifest_date).strip() == "":
            raise ValueError(
                f"CRITICAL: Missing manifest date for flight {flight_number}. "
                f"Cannot safely generate cache key or query API without a timestamp."
            )
        clean_pickup = re.sub(r"[\s\:\-]", "", str(pickup_time_str))
        clean_date = re.sub(r"[\s\:\-]", "", str(manifest_date))
        return f"{flight_number}_{clean_date}_{clean_pickup}"

    def get(self, key: str) -> Optional[dict]:
        return self._data.get(key)

    def set(self, key: str, value: dict) -> None:
        self._data[key] = value
        self._save()


# --- Back-compat functional API (kept in case other scripts import these directly) ---

def load_cache(cache_file: Union[str, Path]) -> Dict[str, dict]:
    return FlightCache(cache_file)._data


def save_cache(cache_data: Dict[str, dict], cache_file: Union[str, Path]) -> None:
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Could not write to cache file: {e}")


def generate_shuttle_cache_key(flight_number: str, pickup_time_str: str, manifest_date: str) -> str:
    return FlightCache.make_key(flight_number, pickup_time_str, manifest_date)
