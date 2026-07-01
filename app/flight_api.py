"""
AeroDataBox flight-status client, routed through api.market.

This isolates all networking (requests, rate-limit sleeps, cache lookups) from
the pure text parsing in app.extraction and the CSV/row wrangling in
app.pipeline. Nothing here knows what a CSV row looks like -- it only knows
how to answer "what's the status of flight X, and which leg matches the
pickup time Y" and to cache the answer.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import List, Optional

import requests

from app.cache import FlightCache

logger = logging.getLogger(__name__)

AERODATABOX_URL = "https://prod.api.market/api/v1/aedbx/aerodatabox/flights/number/{flight_number}/{date}"
REQUEST_THROTTLE_SECONDS = 1.5


@dataclass
class FlightStatus:
    status: str
    origin: str
    scheduled_arrival: str


class AeroDataBoxClient:
    def __init__(
        self,
        api_key: str,
        arrival_iata: str,
        cache: Optional[FlightCache] = None,
        use_cache: bool = True,
    ):
        self.api_key = api_key
        self.arrival_iata = arrival_iata.strip().upper()
        self.use_cache = use_cache
        self.cache = cache

    # -- public API ---------------------------------------------------------

    def get_flight_status(
        self, flight_number: Optional[str], pickup_time_str: str, manifest_date: str
    ) -> FlightStatus:
        """Looks up (cache-first) the flight leg landing at our target airport."""
        if not flight_number:
            return FlightStatus("N/A", "N/A", "N/A")

        cache_key = None
        if self.use_cache and self.cache is not None:
            cache_key = FlightCache.make_key(flight_number, pickup_time_str, manifest_date)
            cached = self.cache.get(cache_key)
            if cached:
                logger.info(f"[CACHE HIT] {flight_number} ({cache_key})")
                return FlightStatus(cached["status"], cached["origin"], cached["sched_arr"])
            logger.info(f"[CACHE MISS] {flight_number} ({cache_key}) -- querying api.market...")

        try:
            result = self._resolve_from_api(flight_number, pickup_time_str, manifest_date)
        except Exception as e:
            logger.error(f"[FETCH FAILED] Error resolving flight {flight_number}: {e}")
            return FlightStatus(f"Fetch Error: {e}", "N/A", "N/A")

        if cache_key and self.cache is not None:
            self.cache.set(cache_key, {
                "status": result.status,
                "origin": result.origin,
                "sched_arr": result.scheduled_arrival,
            })

        return result

    # -- internals ------------------------------------------------------------

    def _resolve_from_api(self, flight_number: str, pickup_time_str: str, manifest_date: str) -> FlightStatus:
        api_data = self._fetch_live_payload(flight_number, manifest_date)
        if not api_data:
            return FlightStatus("No data found", "N/A", "N/A")

        raw_legs = api_data if isinstance(api_data, list) else api_data.get("legs", [api_data])

        matched_legs = [leg for leg in raw_legs if self._leg_matches_destination(leg)]

        if not matched_legs:
            bad_leg = raw_legs[0]
            actual_dest = bad_leg.get("arrival", {}).get("airport", {}).get("iata", "UNK")
            logger.warning(
                f"[ROUTE REJECTED] Flight {flight_number} lands in {actual_dest} "
                f"instead of {self.arrival_iata}."
            )
            origin = bad_leg.get("departure", {}).get("airport", {}).get("name", "Unknown").replace(" ", "\n")
            return FlightStatus("Mismatch", origin, "INVALID\nDESTINATION")

        if len(matched_legs) > 1:
            logger.info(f"[CONNECTING ROUTE] {len(matched_legs)} target legs found, scoring proximity...")
            target_leg = self._pick_best_leg(matched_legs, pickup_time_str)
        else:
            target_leg = matched_legs[0]

        status = target_leg.get("status", "Unknown")
        origin = target_leg.get("departure", {}).get("airport", {}).get("name", "Unknown").replace(" ", "\n")
        plain_sched_arr = target_leg.get("arrival", {}).get("scheduledTime", {}).get("local", "N/A")

        from app.extraction import format_timezone_offset  # local import avoids a cycle at module load
        sched_arr = format_timezone_offset(plain_sched_arr).replace(" ", "\n")

        return FlightStatus(status, origin, sched_arr)

    def _leg_matches_destination(self, leg: dict) -> bool:
        arrival = leg.get("arrival", {})
        airport = arrival.get("airport", {})
        candidates = (
            airport.get("name", "").upper(),
            airport.get("iata", "").upper(),
            airport.get("municipalityName", "").upper(),
        )
        return any(self.arrival_iata in c for c in candidates)

    def _pick_best_leg(self, legs: List[dict], pdf_pickup_time_str: str) -> dict:
        """Of multiple connecting legs, picks the one arriving closest to the target pickup time."""
        p_match = re.search(r"(\d{1,2}):(\d{2})", str(pdf_pickup_time_str))
        if not p_match:
            return legs[0]

        pickup_minutes = int(p_match.group(1)) * 60 + int(p_match.group(2))
        best_leg = legs[0]
        min_delta = float("inf")

        for leg in legs:
            arrival_local = leg.get("arrival", {}).get("scheduledTime", {}).get("local", "")
            dest = leg.get("arrival", {}).get("airport", {}).get("iata", "UNK")
            arr_match = re.search(r"[\sT](\d{2}):(\d{2})", arrival_local)
            if not arr_match:
                continue

            api_minutes = int(arr_match.group(1)) * 60 + int(arr_match.group(2))
            delta = abs(pickup_minutes - api_minutes)
            logger.info(f"[PROXIMITY EVAL] {dest} arrives {arr_match.group(0).strip()} | delta: {delta} mins")

            if delta < min_delta:
                min_delta = delta
                best_leg = leg

        logger.info(
            f"[MATCH LOCKED] {best_leg.get('arrival', {}).get('airport', {}).get('iata')} "
            f"at {best_leg.get('arrival', {}).get('scheduledTime', {}).get('local')}"
        )
        return best_leg

    def _fetch_live_payload(self, flight_number: str, manifest_date: str):
        logger.info(f"[API ROUTE] Querying api.market for flight: {flight_number}")
        time.sleep(REQUEST_THROTTLE_SECONDS)

        url = AERODATABOX_URL.format(flight_number=flight_number, date=manifest_date)
        headers = {"x-api-market-key": self.api_key}

        response = requests.get(url, headers=headers)
        if response.status_code == 204:
            return []
        response.raise_for_status()
        return response.json()
