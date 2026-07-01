"""
Pure extraction/parsing helpers.

Everything in this file is a plain function: text in, text out. No network
calls, no file I/O, no globals. That was one of the biggest tangles in the
original flight_checker.py -- text parsing, HTTP calls, and caching were all
interleaved in the same 400-line file, which made it hard to test or change
one without risking the others. Import this module and you get zero side
effects and no environment/API-key requirements.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

from app.models import ManifestRow

logger = logging.getLogger(__name__)

_FLIGHT_CODE_PATTERN = re.compile(
    r"(?:(?:航班号|航班):\s*)([A-Za-z0-9]+)|\b([A-Za-z]{2,3}\d{1,4})\b"
)


def normalize_date_for_api(raw_date_str: Optional[str]) -> str:
    """Ensures a date string matches the YYYY-MM-DD format required by AeroDataBox."""
    if not raw_date_str:
        return ""

    cleaned = str(raw_date_str).strip().replace("/", "-")

    if re.match(r"^\d{4}-\d{2}-\d{2}$", cleaned):
        return cleaned

    # MM-DD (e.g. "06-29") -> assume the current operational year
    if re.match(r"^\d{2}-\d{2}$", cleaned):
        return f"2026-{cleaned}"

    return cleaned


def extract_full_flight_code(cell_text: Optional[str]) -> Optional[str]:
    """
    Parses raw manifest text to extract a flight code. Handles Chinese-language
    prefixes ('航班:', '航班号:') as well as standalone codes like 'UA764'.
    Returns the code in uppercase, or None if nothing recognizable is found.
    """
    if not cell_text or not isinstance(cell_text, str):
        return None

    cell_text = cell_text.strip()
    match = _FLIGHT_CODE_PATTERN.search(cell_text)
    if match:
        code = match.group(1) or match.group(2)
        return code.upper()

    if "航班" in cell_text:
        logger.warning(f"Keyword '航班' found, but no valid code extracted from: '{cell_text}'")

    return None


_TZ_ABBREVIATIONS = {
    "-04:00": "EDT", "-05:00": "EST/CDT", "-06:00": "MDT/CST",
    "-07:00": "MST/PDT", "-08:00": "PST", "Z": "UTC", "+00:00": "UTC",
}


def format_timezone_offset(time_str: Optional[str]) -> str:
    """Maps ISO timezone offsets (e.g. '-06:00') to readable acronyms (e.g. 'MDT/CST')."""
    if not time_str or time_str == "N/A":
        return "N/A"

    cleaned = time_str.split(".")[0].replace("T", " ")
    for offset, abbreviation in _TZ_ABBREVIATIONS.items():
        if offset in cleaned:
            return cleaned.replace(offset, f" {abbreviation}")
    return cleaned[:16]


def calculate_wait_time(arrival_text: Optional[str], pickup_text: Optional[str]) -> str:
    """Computes the delta (pickup time minus arrival time) in minutes, as display text."""
    if not arrival_text or not pickup_text or "N/A" in arrival_text or "INVALID" in arrival_text:
        return "N/A"
    try:
        arr_match = re.search(r"(\d{1,2}):(\d{2})", arrival_text)
        pickup_match = re.search(r"(\d{1,2}):(\d{2})", pickup_text)
        if not (arr_match and pickup_match):
            return "N/A"

        arr_minutes = int(arr_match.group(1)) * 60 + int(arr_match.group(2))
        pickup_minutes = int(pickup_match.group(1)) * 60 + int(pickup_match.group(2))

        delta_minutes = pickup_minutes - arr_minutes
        if delta_minutes < -600:  # overnight wrap correction
            delta_minutes += 1440
        return f"{delta_minutes} min"
    except Exception:
        return "N/A"


def identify_key_columns(columns: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Finds the manifest's flight-info column and pickup-time column by header name
    (case/whitespace-insensitive), returning the *original header text* for each
    so callers can look values up via ManifestRow.get(header).
    """
    flt_info_col: Optional[str] = None
    pickup_col: Optional[str] = None

    for header in columns:
        if not header:
            continue
        clean = re.sub(r"[\s\-]", "", str(header)).upper()
        if "FLTINFO" in clean:
            flt_info_col = header
        if any(token in clean for token in ("PICKTIME", "PICKUP")):
            pickup_col = header

    return flt_info_col, pickup_col


def find_manifest_date(rows: List[ManifestRow], flt_info_col: str) -> Optional[str]:
    """
    Scans manifest rows for an embedded operational date, as a last resort when
    no manifest date was supplied explicitly (via CLI arg, sidebar, or env var).
    """
    for row in rows:
        text = str(row.get(flt_info_col)).strip()

        date_match = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2})", text)
        if date_match:
            found = date_match.group(1).replace("/", "-")
            logger.info(f"[DATE DETECTED] Locked target manifest date: {found}")
            return found

        month_match = re.search(r"\d{1,2}[-\s][A-Za-z]{3}", text)
        if month_match:
            logger.info(f"[DATE DETECTED] Locked target manifest date: {month_match.group(0)}")
            return month_match.group(0)

    logger.warning("[DATE WARNING] No operational date found in the flight-info column.")
    return None
