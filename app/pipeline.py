"""
Stage 1: reads the manifest CSV and enriches each row with live flight data.

This is the orchestration layer -- it doesn't parse text itself (that's
app.extraction) and it doesn't talk to the API itself (that's app.flight_api).
It just wires the two together per row and hands back typed ManifestRow
objects plus the output column order to use for CSV/PDF export.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from app.cache import FlightCache
from app.config import Settings
from app.csv_io import build_output_columns, read_manifest_csv
from app.extraction import (
    calculate_wait_time,
    extract_full_flight_code,
    find_manifest_date,
    identify_key_columns,
    normalize_date_for_api,
)
from app.flight_api import AeroDataBoxClient
from app.models import ManifestRow

logger = logging.getLogger(__name__)


def run_extraction_pipeline(
    settings: Settings,
    csv_path: Optional[Path] = None,
) -> Tuple[List[str], List[ManifestRow]]:
    """
    Reads `csv_path` (or settings.input_csv) and enriches every row with live
    flight status via AeroDataBox. Returns (output_columns, rows) ready for
    CSV/PDF export or the Stage 2 optimizer.
    """
    source_csv = Path(csv_path) if csv_path else settings.input_csv
    columns, rows = read_manifest_csv(source_csv)

    flt_info_col, pickup_col = identify_key_columns(columns)
    if flt_info_col is None:
        raise ValueError("Could not find a 'FLT Info' column in the uploaded manifest.")

    manifest_date = settings.manifest_date
    if manifest_date:
        manifest_date = normalize_date_for_api(manifest_date)
        logger.info(f"[DATE] Manifest date set by caller: {manifest_date}")
    else:
        manifest_date = find_manifest_date(rows, flt_info_col)

    cache = FlightCache(settings.cache_file) if settings.use_cache else None
    client = AeroDataBoxClient(
        api_key=settings.apimarket_key,
        arrival_iata=settings.arrival_iata,
        cache=cache,
        use_cache=settings.use_cache,
    )

    for row in rows:
        row.flight_code = extract_full_flight_code(row.get(flt_info_col))
        row.pickup_time = row.get(pickup_col) if pickup_col else None

        result = client.get_flight_status(row.flight_code, row.pickup_time or "", manifest_date or "")
        row.status = result.status
        row.origin_airport = result.origin
        row.scheduled_arrival = result.scheduled_arrival

        if row.pickup_time is not None:
            if "INVALID" in str(row.scheduled_arrival) or row.status == "Mismatch":
                row.wait_time = "N/A"
            else:
                row.wait_time = calculate_wait_time(row.scheduled_arrival, row.pickup_time)

    output_columns = build_output_columns(columns, flt_info_col, pickup_col)
    return output_columns, rows
