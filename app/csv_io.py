"""
CSV input/output for manifest data.

Reading now goes through csv.DictReader instead of csv.reader, so a row's
values are addressable by header name right from the start (row['FLT Info'])
rather than by position (row[7]). Writing is centralized here too: the output
column order (where "Flight Code", "Wait time", "Pickup Group ID" etc. get
inserted relative to the original columns) is computed once in
`build_output_columns`, instead of being re-derived via `header.insert(idx, ...)`
calls scattered across extraction and optimization.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from app.models import ManifestRow

logger = logging.getLogger(__name__)


def read_manifest_csv(csv_path: Path) -> Tuple[List[str], List[ManifestRow]]:
    """Reads the uploaded manifest CSV into (original column names, typed rows)."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Input file not found: {csv_path.absolute()}")

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        columns = list(reader.fieldnames or [])
        rows = [ManifestRow(original=dict(raw)) for raw in reader]

    return columns, rows


def build_output_columns(
    original_columns: List[str],
    flt_info_col: str,
    pickup_col: Optional[str],
    include_pickup_groups: bool = False,
) -> List[str]:
    """
    Computes the export column order: original columns, with API-enriched fields
    inserted right after the flight-info column and "Wait time" right after the
    pickup-time column, optionally prefixed with the pickup-optimization columns.
    """
    columns: List[str] = []
    for col in original_columns:
        columns.append(col if col != pickup_col else "OP pickup time")
        if col == flt_info_col:
            columns.extend(["Flight Code", "Arrival", "Status", "Origin Airport"])
        if pickup_col is not None and col == pickup_col:
            columns.append("Wait time")

    if include_pickup_groups:
        columns = ["Pickup Group ID", "Target Vehicle Dispatch", "Passenger Wait Time"] + columns

    return columns


def write_manifest_csv(rows: List[ManifestRow], columns: List[str], output_path: Path) -> bool:
    """Writes typed rows back out to CSV, in `columns` order."""
    if not rows:
        logger.error("CSV output aborted: no rows to write.")
        return False

    try:
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            for row in rows:
                writer.writerow(row.to_row(columns))

        logger.info(f"CSV export complete: {len(rows)} records to {output_path}")
        return True
    except IOError as e:
        logger.error(f"CSV write failure on {output_path}: {e}")
        return False


def verify_row_count(expected_count: int, output_csv_path: Path) -> bool:
    """Sanity-checks that no rows were silently dropped between stages."""
    try:
        with open(output_csv_path, "r", encoding="utf-8", newline="") as f:
            actual_count = sum(1 for _ in csv.reader(f)) - 1  # minus header

        logger.info(f"[INTEGRITY CHECK] Expected: {expected_count} | Found: {actual_count}")
        if expected_count == actual_count:
            logger.info("Integrity check passed.")
            return True

        logger.error(f"CRITICAL MISMATCH: expected {expected_count}, found {actual_count}")
        return False
    except Exception as e:
        logger.error(f"Could not complete integrity check: {e}")
        return False
