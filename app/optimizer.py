"""
Stage 2: groups passengers into shuttle pickup windows.

Same grouping/splitting algorithm as the original optimize_pickups.py, but it
now mutates ManifestRow.group_id / .dispatch_time / .passenger_wait fields
directly instead of doing `row.insert(0, group_name)` / `row.insert(1, ...)`
positional surgery on a list. Splitting an oversized passenger row across two
vehicles is expressed with `dataclasses.replace`, which is far less error
prone than the previous `copy.deepcopy(row)` + re-inserting the same values.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import timedelta
from typing import List

from app.models import ManifestRow

logger = logging.getLogger(__name__)


def build_pickup_groups(
    rows: List[ManifestRow],
    max_wait_hours: float = 2,
    max_capacity: int = 10,
) -> List[ManifestRow]:
    """
    Sorts passengers by customs-clearing "ready time" and packs them into
    shuttle groups bounded by `max_wait_hours` and `max_capacity` seats.
    Rows with no parseable arrival time are flagged for manual review.
    Returns a flat, ordered list of rows with group fields populated.
    """
    valid: List[tuple] = []
    unassigned: List[ManifestRow] = []

    for row in rows:
        ready_dt = row.ready_time()
        if ready_dt is not None:
            valid.append((ready_dt, row.passenger_count(), row))
        else:
            unassigned.append(row)

    valid.sort(key=lambda entry: entry[0])

    max_wait_delta = timedelta(hours=max_wait_hours)
    output: List[ManifestRow] = []
    group_id = 0

    current_group: List[ManifestRow] = []
    current_capacity = 0
    anchor_time = None

    def flush_group():
        nonlocal group_id, current_group, current_capacity, anchor_time
        if not current_group:
            return
        group_id += 1
        dispatch_time = anchor_time + max_wait_delta
        for member in current_group:
            member.group_id = f"Group #{group_id}"
            member.dispatch_time = dispatch_time
            member.passenger_wait = _passenger_wait_str(member, dispatch_time)
        output.extend(current_group)
        current_group = []
        current_capacity = 0
        anchor_time = None

    for ready_dt, p_count, row in valid:
        if not current_group:
            current_group = [row]
            current_capacity = p_count
            anchor_time = ready_dt
            continue

        within_window = (ready_dt - anchor_time) <= max_wait_delta

        if not within_window:
            flush_group()
            current_group = [row]
            current_capacity = p_count
            anchor_time = ready_dt
            continue

        if current_capacity + p_count <= max_capacity:
            current_group.append(row)
            current_capacity += p_count
            continue

        # Row doesn't fully fit -- split it across this vehicle and the next.
        available_seats = max_capacity - current_capacity
        if available_seats > 0:
            current_group.append(replace(row))  # fills remaining seats in this vehicle
            flush_group()
            remainder_count = p_count - available_seats
            current_group = [replace(row)]  # carries the remainder to the next vehicle
            current_capacity = remainder_count
            anchor_time = ready_dt
        else:
            flush_group()
            current_group = [row]
            current_capacity = p_count
            anchor_time = ready_dt

    flush_group()

    for row in unassigned:
        row.needs_manual_review = True
        row.group_id = "MANUAL REVIEW"
        row.passenger_wait = "N/A"

    return output + unassigned


def _passenger_wait_str(row: ManifestRow, dispatch_time) -> str:
    """How long this passenger waits between being customs-ready and vehicle dispatch."""
    ready_dt = row.ready_time()
    if ready_dt is None or dispatch_time is None:
        return "N/A"
    wait_minutes = int((dispatch_time - ready_dt).total_seconds() / 60)
    return f"{wait_minutes} min"


def run_optimization_pipeline(
    rows: List[ManifestRow],
    max_wait_hours: float = 2,
    max_capacity: int = 10,
) -> List[ManifestRow]:
    """Public entry point for Stage 2, kept for symmetry with run_extraction_pipeline."""
    if not rows:
        logger.info("No rows passed to the optimization stage.")
        return []

    logger.info("Starting Stage 2: grouping passenger schedules...")
    return build_pickup_groups(rows, max_wait_hours=max_wait_hours, max_capacity=max_capacity)
