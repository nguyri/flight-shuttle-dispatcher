"""
Command-line entry point.

Equivalent to the old `if __name__ == "__main__":` block in flight_checker.py,
but pulled out into its own file so `app.pipeline` / `app.flight_api` etc. can
be imported by Streamlit (or tests) without argparse or CLI-only concerns
tagging along.

Usage:
    python -m app.cli --csv data/manifest.csv --iata YYC --manifest_date 2026-06-30 --pdf
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.config import load_settings_from_env
from app.csv_io import verify_row_count, write_manifest_csv
from app.optimizer import run_optimization_pipeline
from app.pdf_output import save_pipeline_to_pdf
from app.pipeline import run_extraction_pipeline

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest_date", type=str, help="Override manifest date (YYYY-MM-DD)")
    parser.add_argument("--csv", type=str, help="Override input CSV path")
    parser.add_argument("--iata", type=str, help="Override arrival IATA code")
    parser.add_argument("--pdf", action="store_true", help="Also export results as a PDF report")
    args = parser.parse_args()

    settings = load_settings_from_env(arrival_iata=args.iata, manifest_date=args.manifest_date)
    logger.setLevel(logging.DEBUG if settings.verbose_logging else logging.INFO)

    csv_path = Path(args.csv) if args.csv else settings.input_csv

    columns, rows = run_extraction_pipeline(settings, csv_path=csv_path)
    if not rows:
        logger.error("No rows extracted. Aborting.")
        return

    write_manifest_csv(rows, columns, settings.parse_csv)
    verify_row_count(len(rows), settings.parse_csv)

    optimized_rows = run_optimization_pipeline(rows)
    output_columns = ["Pickup Group ID", "Target Vehicle Dispatch", "Passenger Wait Time"] + columns

    write_manifest_csv(optimized_rows, output_columns, settings.output_csv)
    verify_row_count(len(optimized_rows), settings.output_csv)

    if args.pdf:
        pdf_path = settings.output_csv.with_suffix(".pdf")
        save_pipeline_to_pdf(optimized_rows, output_columns, pdf_path, settings.manifest_date)


if __name__ == "__main__":
    main()
