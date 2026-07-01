"""
Data model for a single manifest entry.

The old pipeline represented every passenger row as a plain `list[str]`, and
every stage (extraction, optimization, PDF export) located fields by calling
`next(i for i, h in enumerate(header) if "ARRIVAL" in h.upper())` and then
doing `row.insert(idx, value)` or `row[idx]`. That's fragile: insert one column
in the wrong stage and every downstream index shifts silently, and there's no
way to tell what a row *contains* without cross-referencing the header list.

ManifestRow replaces that with named attributes. Original CSV columns (which
vary and aren't fully known ahead of time -- "No.", "AGE", free-form pickup
columns, etc.) are kept in `.original`, a dict keyed by header name, so they're
still accessible by name without brittle index math. Everything the pipeline
itself computes (flight status, wait times, pickup grouping) is a first-class
typed field.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# Canadian / transborder hubs treated as domestic (30 min customs clearing window)
# instead of international (60 min).
DOMESTIC_AIRPORT_CODES = {"YYC", "YVR", "YUL", "YYZ", "YEG", "YOW", "YWG", "YHZ"}
DOMESTIC_KEYWORDS = ("VANCOUVER", "MONTREAL", "TORONTO", "WINNIPEG", "HALIFAX", "EDMONTON", "OTTAWA")


@dataclass
class ManifestRow:
    """One passenger/flight entry from the uploaded manifest CSV."""

    # Original CSV columns, keyed by header name. Never mutated once loaded.
    original: Dict[str, str] = field(default_factory=dict)

    # Populated by the extraction stage (app.pipeline / app.flight_api)
    flight_code: Optional[str] = None
    status: Optional[str] = None
    origin_airport: Optional[str] = None
    scheduled_arrival: Optional[str] = None  # display string, e.g. "2026-06-29\n11:32\nMDT/CST"
    pickup_time: Optional[str] = None        # renamed "OP pickup time" column value
    wait_time: Optional[str] = None          # pickup vs. arrival delta, display string

    # Populated by the optimization stage (app.optimizer)
    group_id: Optional[str] = None
    dispatch_time: Optional[datetime] = None
    passenger_wait: Optional[str] = None
    needs_manual_review: bool = False

    def get(self, column_name: str, default: str = "") -> str:
        """Look up an original CSV column by name."""
        return self.original.get(column_name, default)

    def arrival_datetime(self) -> Optional[datetime]:
        """Parses `scheduled_arrival` into a datetime, ignoring timezone text."""
        text = self.scheduled_arrival
        if not text or "N/A" in text or "INVALID" in text:
            return None
        try:
            clean = text.replace("\n", " ").strip()
            base = " ".join(clean.split()[:2])
            return datetime.strptime(base, "%Y-%m-%d %H:%M")
        except Exception:
            return None

    def is_international(self) -> bool:
        """Flights not originating in Canada get a longer customs clearing window."""
        origin_text = (self.origin_airport or "").strip().upper()
        if any(code in origin_text for code in DOMESTIC_AIRPORT_CODES):
            return False
        if any(keyword in origin_text for keyword in DOMESTIC_KEYWORDS):
            return False
        return True

    def ready_time(self) -> Optional[datetime]:
        """When passengers are expected to clear customs and be curbside-ready."""
        arr = self.arrival_datetime()
        if arr is None:
            return None
        buffer_minutes = 60 if self.is_international() else 30
        return arr + timedelta(minutes=buffer_minutes)

    def passenger_count(self) -> int:
        """
        Counts passengers via the "No." column, falling back to "AGE".
        Both are comma-separated lists of passenger entries in the source manifest.
        """
        for key_fragment in ("NO.", "AGE"):
            for header, value in self.original.items():
                if header and key_fragment in header.upper():
                    value = str(value).strip()
                    if value and value != "N/A":
                        return len([item for item in value.split(",") if item.strip()])
        return 1  # baseline fallback if the columns are missing entirely

    def to_dict(self, columns: List[str]) -> Dict[str, str]:
        """Flattens this row into a plain dict following the given column order."""
        out: Dict[str, str] = {}
        for col in columns:
            if col == "Pickup Group ID":
                out[col] = self.group_id or ""
            elif col == "Target Vehicle Dispatch":
                out[col] = self.dispatch_time.strftime("%Y-%m-%d %H:%M") if self.dispatch_time else "N/A - Review Flight"
            elif col == "Passenger Wait Time":
                out[col] = self.passenger_wait or "N/A"
            elif col == "Flight Code":
                out[col] = self.flight_code or ""
            elif col == "Arrival":
                out[col] = self.scheduled_arrival or ""
            elif col == "Status":
                out[col] = self.status or ""
            elif col == "Origin Airport":
                out[col] = self.origin_airport or ""
            elif col == "Wait time":
                out[col] = self.wait_time or ""
            elif col == "OP pickup time":
                out[col] = self.pickup_time if self.pickup_time is not None else self.get(col)
            else:
                out[col] = self.get(col)
        return out

    def to_row(self, columns: List[str]) -> List[str]:
        """Flattens this row into a list following the given column order (for CSV/PDF export)."""
        d = self.to_dict(columns)
        return [d.get(col, "") for col in columns]
