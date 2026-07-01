"""
Centralized pipeline configuration.

Previously the API key, target IATA code, and manifest date lived as module-level
globals in flight_checker.py (ARRIVAL_IATA_CODE, MANIFEST_DATE, APIMARKET_KEY).
That's a real bug in a Streamlit app: Streamlit can serve multiple browser
sessions against the same Python process, and every session was mutating the
same global state. Two people running the pipeline for different dates/airports
at the same time would stomp on each other's requests mid-flight.

Settings is now an explicit, immutable-ish value object created per pipeline run
and threaded through function calls instead.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    apimarket_key: str
    arrival_iata: str = "YYC"
    manifest_date: Optional[str] = None  # YYYY-MM-DD, normalized later
    use_cache: bool = True
    cache_file: Path = field(default_factory=lambda: Path("flight_cache.json"))
    verbose_logging: bool = True

    input_csv: Path = field(default_factory=lambda: Path("data/flights-3.csv"))
    parse_csv: Path = field(default_factory=lambda: Path("data/parse_output.csv"))
    output_csv: Path = field(default_factory=lambda: Path("data/flights_output.csv"))


def load_settings_from_env(
    *,
    arrival_iata: Optional[str] = None,
    manifest_date: Optional[str] = None,
) -> Settings:
    """
    Builds a Settings instance from environment variables, with optional
    per-call overrides (e.g. values coming from the Streamlit sidebar or CLI args).

    Raises ValueError if APIMARKET_KEY is missing -- but only when a Settings
    object is actually requested, not just at import time. This means importing
    app.pipeline / app.flight_api no longer crashes a process that hasn't set
    the env var yet (useful for tests, notebooks, `python -c "import app"`, etc).
    """
    apimarket_key = os.environ.get("APIMARKET_KEY")
    if not apimarket_key:
        raise ValueError(
            "CRITICAL ERROR: APIMARKET_KEY is missing from environment variables!"
        )

    verbose = os.environ.get("VERBOSE_LOGGING", "True").lower() in ("true", "1", "yes")

    return Settings(
        apimarket_key=apimarket_key,
        arrival_iata=(arrival_iata or os.environ.get("ARRIVAL_IATA_CODE", "YYC")).strip().upper(),
        manifest_date=manifest_date or os.environ.get("MANIFEST_DATE"),
        use_cache=True,
        cache_file=Path(os.environ.get("CACHE_FILE", "flight_cache.json")),
        verbose_logging=verbose,
        input_csv=Path(os.environ.get("INPUT_CSV", "data/flights-3.csv")),
        parse_csv=Path(os.environ.get("PARSE_CSV", "data/parse_output.csv")),
        output_csv=Path(os.environ.get("OUTPUT_CSV", "data/flights_output.csv")),
    )
