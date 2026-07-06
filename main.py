import io
import json
import logging
import sys
from pathlib import Path
import tempfile
import pandas as pd
import streamlit as st
import datetime

from app import ai_parser
from app.config import load_settings_from_env
from app.extraction import guess_name_column
from app.optimizer import run_optimization_pipeline
from app.pdf_output import save_pipeline_to_pdf
from app.ai_parser import generate_prompt, extract_table_from_image
from app.pipeline import run_extraction_pipeline
from app.viewer import render_interactive_passenger_table

# Configuration paths for both caches
FLIGHT_CACHE_PATH = Path("flight_cache.json")
PASSENGER_CACHE_PATH = Path("passenger_cache.json")
TEMP_DIR = Path(tempfile.gettempdir()) 
TEMP_CSV_PATH = TEMP_DIR / "temp_manifest.csv"

TEMP_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

st.set_page_config(page_title="Flight & Passenger Dispatcher", layout="wide")
st.title("✈️ Flight Checker & Cache Manager")

MANIFEST_HEADERS = [
    "No.", "Invoice", "Name (Last First)", "AGE", "Contact Info", "FLT Info", 
    "Pick Time", "Pick-up", "Drop-off", "Meal", "Play Option / Activities", 
    "Room", "Next Itinerary", "OP Note"
]

with st.expander("📋 **Image to CSV Prompt:**"):
    st.code(ai_parser.generate_prompt(target_headers=MANIFEST_HEADERS))

# Sidebar routing configuration selector
app_mode = st.sidebar.radio("Choose Mode", ["Run Flight Checker", "View Cache"])

if app_mode == "Run Flight Checker":
    st.subheader("Run Flight Checker")

    st.sidebar.header("Checker Configurations")
    arrival_iata = st.sidebar.text_input("Target Arrival IATA Code", value="YYC").upper()
    target_date = st.sidebar.date_input("Target Operational Date")
    max_wait = st.sidebar.slider("Maximum Passenger Wait Window (Hours)", 1.0, 4.0, 2.0, step=0.5)

 # UI routing option
    input_type = st.radio("Select Input Manifest Source:", ["Upload CSV", "Upload Image(s) (AI Extraction)"], horizontal=True)
    
    # Initialize state keys if they don't exist yet
    if "df_payload" not in st.session_state:
        st.session_state["df_payload"] = None

    if input_type == "Upload CSV":
        uploaded_file = st.file_uploader("Choose a Manifest CSV file", type=["csv"])
        if uploaded_file is not None:
            # Load right into pandas immediately
            try:
                st.session_state["df_payload"] = pd.read_csv(uploaded_file)
                # Keep backup file path mirror intact
                st.session_state["df_payload"].to_csv(TEMP_CSV_PATH, index=False)
            except Exception as e:
                st.error(f"Failed to read CSV: {e}")
    else:
        uploaded_images = st.file_uploader("Upload Image(s) of the Manifest Table", type=["png", "jpg", "jpeg"], accept_multiple_files=True)
        
        if uploaded_images:
            st.info(f"📸 {len(uploaded_images)} image(s) staged for AI extraction.")
            
            if st.button("🤖 AI Extract All Tables"):
                with st.spinner("Gemini is processing, merging, and writing the manifest payload to disk..."):
                    try:
                        from app.ai_parser import batch_extract_and_save_csv
                        
                        # Extract and save internally
                        combined_df = batch_extract_and_save_csv(
                            image_files=uploaded_images, 
                            target_headers=MANIFEST_HEADERS, 
                            output_csv_path=TEMP_CSV_PATH
                        )
                        
                        # Cache the ENTIRE dataframe structure inside session state!
                        st.session_state["df_payload"] = combined_df
                        st.success("🎉 AI successfully processed and generated your operational manifest CSV file!")
                        
                    except Exception as e:
                        st.error(f"Pipeline Interrupted: {e}")

    # Display data preview whenever data exists in state memory (Removes the .head(5) limitation)
    if st.session_state["df_payload"] is not None:
        st.markdown(f"### Current Staged Manifest Preview (Total Rows: {len(st.session_state['df_payload'])})")
        st.dataframe(st.session_state["df_payload"])

        # Calculate dynamic metrics for the cache indicator badge
        row_count = len(st.session_state["df_payload"])
        source_label = "AI Vision Extraction" if input_type != "Upload CSV" else "CSV Upload"
        
        row_count = len(st.session_state["df_payload"])
        abs_path = TEMP_CSV_PATH.resolve()
        st.success(
            f"✅ **CSV Cache Ready** | `{row_count}` rows staged in memory.\n\n"
            f"📍 **Target File Path:** `{abs_path}`"
        )

        if st.button("🚀 Check Flights"):
            with st.spinner("Executing pipeline modules..."):
                
                # Make SURE the file on disk perfectly matches the current active state memory
                st.session_state["df_payload"].to_csv(TEMP_CSV_PATH, index=False)

                date_str = target_date.strftime("%Y-%m-%d")

                try:
                    settings = load_settings_from_env(arrival_iata=arrival_iata, manifest_date=date_str)
                except ValueError as e:
                    st.error(str(e))
                    st.stop()

                # Execute core business logic modules
                columns, rows = run_extraction_pipeline(settings, csv_path=TEMP_CSV_PATH)
                optimized_rows = run_optimization_pipeline(rows, max_wait_hours=max_wait)
                output_columns = ["Pickup Group ID", "Target Vehicle Dispatch", "Passenger Wait Time"] + columns

            if optimized_rows:
                original_columns = [c for c in output_columns if c not in (
                    "Pickup Group ID", "Target Vehicle Dispatch", "Passenger Wait Time",
                    "Flight Code", "Arrival", "Status", "Origin Airport", "Wait time"
                )]
                name_col = guess_name_column(original_columns) or (original_columns[0] if original_columns else "Passenger Name")
                contact_col = next((c for c in original_columns if "CONTACT" in c.upper() or "PHONE" in c.upper()), None)

                summary_rows = []
                passenger_cache_payload = {}

                for idx, row in enumerate(optimized_rows):
                    is_intl = getattr(row, "is_international", lambda: False)() if hasattr(row, "is_international") else False
                    dispatch_str = row.dispatch_time.strftime("%H:%M") if getattr(row, "dispatch_time", None) else "MANUAL REVIEW"
                    
                    p_name = row.get(name_col, f"Passenger_{idx}") if hasattr(row, "get") else getattr(row, "name", f"Passenger_{idx}")
                    p_phone = row.get(contact_col, "N/A") if contact_col else "N/A"
                    flt_code = getattr(row, "flight_code", "N/A")
                    arr_time = str(getattr(row, "scheduled_arrival", "N/A")).replace("\n", " ")
                    origin = str(getattr(row, "origin_airport", "N/A")).replace("\n", " ")
                    group_id = getattr(row, "group_id", "N/A")

                    record = {
                        "Name": p_name,
                        "Contact Info": p_phone,
                        "Flight Code": flt_code,
                        "Arrival Time": arr_time,
                        "Origin Airport": origin,
                        "International": bool(is_intl),
                        "Dispatch Time": dispatch_str,
                        "Group ID": group_id,
                        "SMS Status": "Not Sent",
                        "Send SMS": False
                    }
                    summary_rows.append(record)
                    
                    p_cache_key = f"{p_name.replace(' ', '')}_{flt_code}_{date_str}_{idx}"
                    passenger_cache_payload[p_cache_key] = record

                with open(PASSENGER_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump(passenger_cache_payload, f, indent=4, ensure_ascii=False)
                st.success(f"Successfully cached {len(passenger_cache_payload)} optimized passenger records!")

                # Render Interactive Layout View
                st.write(f"#### Streamlined Table View (Total Rows: {len(summary_rows)})")
                summary_df = pd.DataFrame(summary_rows)
                render_interactive_passenger_table(summary_df, cache_save_path=PASSENGER_CACHE_PATH, cache_key_prefix="live_pipeline")
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
                # Unroll cache key records mapping cleanly back out into an interactive dataframe footprints
                records = list(p_data.values())
                p_df = pd.DataFrame(records)
                
                # Setup specific viewing column sequence
                cols = ["Name", "Contact Info", "Flight Code", "Dispatch Time","SMS Status", "Send SMS", "Group ID", "Arrival Time", "Origin Airport" ]
                valid_cols = [c for c in cols if c in p_df.columns]
                p_df = p_df[valid_cols]
                
                st.markdown("### 🗄️ Stored Operational Logs Explorer")
                # Direct lookup tracking and message triggers shared out of the same unified table layout function!
                render_interactive_passenger_table(p_df, cache_save_path=PASSENGER_CACHE_PATH, cache_key_prefix="cache_explorer")
                
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