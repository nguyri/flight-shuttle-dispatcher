import io
import json
import logging
import sys
from pathlib import Path
import pandas as pd
import streamlit as st
import datetime

from app.config import load_settings_from_env
from app.extraction import guess_name_column
from app.optimizer import run_optimization_pipeline
from app.pdf_output import save_pipeline_to_pdf
from app.pipeline import run_extraction_pipeline

# Configuration paths for both caches
FLIGHT_CACHE_PATH = Path("flight_cache.json")
PASSENGER_CACHE_PATH = Path("passenger_cache.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

st.set_page_config(page_title="Flight & Passenger Dispatcher", layout="wide")
st.title("✈️ Flight Checker & Cache Manager")

with st.expander("📋 **Image to CSV Prompt:**"):
    st.code(
        "Can you parse the data in these pictures into a single table with headers: "
        "No.\tInvoice\tName (Last First)\tAGE\tContact Info\tFLT Info\tPick Time\t"
        "Pick-up\tDrop-off\tMeal\tPlay Option / Activities\tRoom\tNext Itinerary\tOP Note",
        language="text"
    )

# View Mode Selection
st.subheader("Select View Mode")
if PASSENGER_CACHE_PATH.exists():
    try:
        # Get last modified time of the passenger cache file
        mtime = PASSENGER_CACHE_PATH.stat().st_mtime
        last_updated = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %I:%M:%S %p")
        
        # Display as a subtle green success sub-indicator
        st.caption(f"🟢 **Passenger Cache Status:** Live (Last generated: `{last_updated}`)")
    except Exception:
        st.caption("⚠️ **Passenger Cache Status:** Unable to read file stats.")
else:
    st.caption("🔴 **Passenger Cache Status:** No local cache file found. Run a new pipeline to initialize.")

app_mode = st.radio(label="Select View Mode",options=["Run Dispatch Pipeline", "View Cached Data Files"], horizontal=True, label_visibility="collapsed")

st.markdown("---") # Thin divider line before the rest of the layout logic

if app_mode == "Run Dispatch Pipeline":
    st.subheader("Run Shuttle Dispatch Pipeline")
    
    st.sidebar.header("Pipeline Configurations")
    arrival_iata = st.sidebar.text_input("Target Arrival IATA Code", value="YYC").upper()
    target_date = st.sidebar.date_input("Target Operational Date")
    max_wait = st.sidebar.slider("Maximum Passenger Wait Window (Hours)", 1.0, 4.0, 2.0, step=0.5)

    uploaded_file = st.file_uploader("Choose a Manifest CSV file", type=["csv"])

    if uploaded_file is not None:
        if st.button("🚀 Process Manifest & Optimize Shuttles"):
            with st.spinner("Executing pipeline modules..."):
                with open("temp_manifest.csv", "wb") as f:
                    f.write(uploaded_file.getbuffer())

                date_str = target_date.strftime("%Y-%m-%d")

                try:
                    settings = load_settings_from_env(arrival_iata=arrival_iata, manifest_date=date_str)
                except ValueError as e:
                    st.error(str(e))
                    st.stop()

                # 1. Flight Cache is utilized internally here within the extraction/live lookup phase
                columns, rows = run_extraction_pipeline(settings, csv_path="temp_manifest.csv")
                optimized_rows = run_optimization_pipeline(rows, max_wait_hours=max_wait)
                output_columns = ["Pickup Group ID", "Target Vehicle Dispatch", "Passenger Wait Time"] + columns

            if optimized_rows:
                original_columns = [c for c in output_columns if c not in (
                    "Pickup Group ID", "Target Vehicle Dispatch", "Passenger Wait Time",
                    "Flight Code", "Arrival", "Status", "Origin Airport", "Wait time"
                )]
                name_col = guess_name_column(original_columns) or (original_columns[0] if original_columns else "Passenger Name")

                # Build final presentation data structure
                summary_rows = []
                passenger_cache_payload = {}

                for idx, row in enumerate(optimized_rows):
                    # Check international status dynamically or through row attributes
                    is_intl = getattr(row, "is_international", lambda: False)() if hasattr(row, "is_international") else False
                    dispatch_str = row.dispatch_time.strftime("%Y-%m-%d %H:%M") if getattr(row, "dispatch_time", None) else "MANUAL REVIEW"
                    
                    p_name = row.get(name_col, f"Passenger_{idx}") if hasattr(row, "get") else getattr(row, "name", f"Passenger_{idx}")
                    flt_code = getattr(row, "flight_code", "N/A")
                    arr_time = str(getattr(row, "scheduled_arrival", "N/A")).replace("\n", " ")
                    origin = str(getattr(row, "origin_airport", "N/A")).replace("\n", " ")
                    group_id = getattr(row, "group_id", "N/A")

                    record = {
                        "Name": p_name,
                        "Flight Code": flt_code,
                        "Arrival Time": arr_time,
                        "Origin Airport": origin,
                        "International": bool(is_intl),
                        "Dispatch Time": dispatch_str,
                        "Group ID": group_id
                    }
                    summary_rows.append(record)
                    
                    # Add to passenger cache dictionary using unique name-flight combo or incremental ID
                    p_cache_key = f"{p_name.replace(' ', '')}_{flt_code}_{date_str}_{idx}"
                    passenger_cache_payload[p_cache_key] = record

                # 2. Persist the generated passenger schedule into its separate cache file
                with open(PASSENGER_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump(passenger_cache_payload, f, indent=4)
                st.success(f"Successfully cached {len(passenger_cache_payload)} optimized passenger records to `{PASSENGER_CACHE_PATH.name}`!")

                # Render Table View
                st.write(f"#### Streamlined Table View (Total Rows: {len(summary_rows)})")
                summary_df = pd.DataFrame(summary_rows)
                st.dataframe(summary_df, width='stretch', hide_index=True)
            else:
                st.error("Pipeline failure. Check file formatted logs.")

else:
    # --- Cache Inspection Mode ---
    st.subheader("📂 System Cache Registry Viewer")
    
    cache_type = st.radio("Select Cache to View", options=["Passenger Dispatch Cache", "Flight API Data Cache"], horizontal=True)
    
    if cache_type == "Passenger Dispatch Cache":
        st.markdown(f"Reading from local system path: `{PASSENGER_CACHE_PATH}`")
        if not PASSENGER_CACHE_PATH.exists():
            st.info("No passenger cache file has been generated yet. Run the pipeline first.")
        else:
            with open(PASSENGER_CACHE_PATH, "r", encoding="utf-8") as f:
                p_data = json.load(f)
            
            if not p_data:
                st.warning("Passenger dispatch cache exists but is empty.")
            else:
                # Direct parsing of the cached schema
                records = list(p_data.values())
                p_df = pd.DataFrame(records)
                
                # Reordering to ensure International column visibility
                cols = ["Name", "Flight Code", "Intl.", "Dispatch Time", "Group ID", "Arrival Time", "Origin Airport"]
                # Select only the columns that actually exist in the dataframe
                valid_cols = [c for c in cols if c in p_df.columns]
                p_df = p_df[valid_cols]
                
                st.dataframe(p_df, width='stretch', hide_index=True)
                
    elif cache_type == "Flight API Data Cache":
        st.markdown(f"Reading from local system path: `{FLIGHT_CACHE_PATH}`")
        if not FLIGHT_CACHE_PATH.exists():
            st.info("No flight API data cache file found. Run a live query pipeline to fetch API data.")
        else:
            with open(FLIGHT_CACHE_PATH, "r", encoding="utf-8") as f:
                f_data = json.load(f)
                
            if not f_data:
                st.warning("Flight data cache file is currently empty.")
            else:
                f_records = []
                for uniquely_identified_key, details in f_data.items():
                    # Unique IDs are stored as FlightCode_Date_Time
                    key_parts = uniquely_identified_key.split("_")
                    flt_code = key_parts[0] if len(key_parts) > 0 else "Unknown"
                    
                    f_records.append({
                        "Unique Cache Key": uniquely_identified_key,
                        "Flight Code": flt_code,
                        "Origin Hub": details.get("origin", "N/A"),
                        "Status": details.get("status", "N/A"),
                        "Scheduled Arrival": details.get("sched_arr", "N/A"),
                        "Updated/Actual Arrival": details.get("act_arr", "N/A")
                    })
                
                f_df = pd.DataFrame(f_records)
                st.dataframe(f_df, width='stretch', hide_index=True)