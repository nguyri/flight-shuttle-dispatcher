# Flight Shuttle Dispatcher

Reads a passenger flight manifest CSV, enriches it with live flight status
(via AeroDataBox/api.market), and groups passengers into shuttle pickup
windows respecting a max wait time and vehicle capacity.

## Project layout

```
main.py            Streamlit UI (entry point for the web app)
app/
  cli.py            Command-line entry point (python -m app.cli)
  config.py         Settings: env var loading, no more module-level globals
  models.py         ManifestRow: the typed record every stage operates on
  csv_io.py         CSV read/write + output column ordering
  extraction.py     Pure text parsing (flight codes, dates, timezones) — no I/O
  flight_api.py     AeroDataBoxClient: all networking, isolated from parsing
  cache.py          FlightCache: local JSON cache for API lookups
  pipeline.py        Stage 1 orchestration (extraction + live lookups)
  optimizer.py       Stage 2: pickup grouping / vehicle dispatch windows
  pdf_output.py       PDF export (ReportLab)
Dockerfile
requirements.txt
```

## What changed, and why

**Rows are typed now, not lists.** The old code passed CSV rows around as
plain `list[str]`, and every stage located fields by scanning the header for
a keyword and doing `row[idx]` / `row.insert(idx, value)`. That's fragile —
insert a column in the wrong stage and every downstream index shifts, and you
can't tell what a row *contains* without cross-referencing a separate header
list. Rows are now `ManifestRow` dataclass instances with named fields
(`.flight_code`, `.status`, `.origin_airport`, `.scheduled_arrival`, etc.) and
a `.original` dict for the source CSV's own columns, so you access things by
name (`row.get("No.")`, `row.flight_code`) instead of by position.

**One responsibility per file.**
- `extraction.py` is pure functions — text in, text out, no network, no globals.
  You can unit test flight-code parsing without an API key or a CSV on disk.
- `flight_api.py` only knows how to talk to AeroDataBox and pick the right
  connecting leg. It has no idea what a CSV row looks like.
- `cache.py` is a small `FlightCache` class that loads the JSON cache file
  once and writes through, instead of reloading the whole file from disk on
  every single passenger row (the old `get_flight_live_data()` called
  `load_cache()` fresh per row — O(n) full-file reads for an n-row manifest).
- `csv_io.py` centralizes the read/write and output-column-order logic, so
  there's exactly one place that decides "Flight Code" goes right after
  "FLT Info" and "Wait time" goes right after the pickup column — instead of
  that logic being re-derived independently in extraction, optimization, and
  the Streamlit UI.
- `optimizer.py` mutates `.group_id` / `.dispatch_time` / `.passenger_wait`
  directly on rows instead of doing `row.insert(0, ...)` / `row.insert(1, ...)`
  positional surgery, and uses `dataclasses.replace()` instead of
  `copy.deepcopy()` when splitting an oversized passenger group across two
  vehicles.

**No more shared mutable globals.** `ARRIVAL_IATA_CODE` and `MANIFEST_DATE`
used to be module-level globals mutated by `run_extraction_pipeline()`. In a
Streamlit app, that's a real correctness bug: Streamlit can serve multiple
browser sessions from the same Python process, so two people running the
pipeline for different airports/dates at the same time were overwriting each
other's in-flight request state. `Settings` is now an explicit, immutable
value object built fresh per run and passed as a parameter.

**API key validation moved out of import time.** The original raised
`ValueError` for a missing `APIMARKET_KEY` as soon as `flight_checker.py` was
imported, which meant `main.py` (and any test) would crash on import in an
environment without the key set. `load_settings_from_env()` now raises only
when settings are actually requested, and the Streamlit app catches that and
shows a normal `st.error()` instead of crashing the whole page.

## Running it

**Streamlit UI:**
```bash
streamlit run main.py
```

**CLI:**
```bash
python -m app.cli --csv data/manifest.csv --iata YYC --manifest_date 2026-06-30 --pdf
```

Both require `APIMARKET_KEY` in the environment (or a `.env` file).

## Known behavior carried over from the original implementation

- When a passenger group is too large to fit in the remaining seats of a
  vehicle, the row is duplicated into both vehicles rather than the
  passenger list itself being split. This matches the original
  `optimize_pickups.py` behavior; flag it if you want the split to actually
  partition the "No." column's passenger names.
- Flight-code extraction is regex-based (`[A-Za-z]{2,3}\d{1,4}`) and will
  false-positive on any manifest text that happens to match that shape.
