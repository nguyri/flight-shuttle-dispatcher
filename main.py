import io
import logging
import sys

import pandas as pd
import streamlit as st

from app.config import load_settings_from_env
from app.optimizer import run_optimization_pipeline
from app.pdf_output import save_pipeline_to_pdf
from app.pipeline import run_extraction_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("urllib3").setLevel(logging.WARNING)

st.set_page_config(page_title="Flight Shuttle Dispatcher", layout="wide")

st.title("✈️ Flight Checker")
st.write(
    "Upload your passenger flight manifest CSV to extract live flight statuses, "
    "filter by destination, and optimize vehicle dispatch windows."
)

st.sidebar.header("Pipeline Configurations")
arrival_iata = st.sidebar.text_input("Target Arrival IATA Code", value="YYC").upper()
target_date = st.sidebar.date_input("Target Operational Date")
max_wait = st.sidebar.slider("Maximum Passenger Wait Window (Hours)", 1.0, 4.0, 2.0, step=0.5)

uploaded_file = st.file_uploader("Choose a Manifest CSV file", type=["csv"])

if uploaded_file is not None:
    st.success("CSV Uploaded Successfully!")

    if st.button("🚀 Check Flights & Shuttles"):
        with st.spinner("Processing Stage 1 & 2: Parsing CSV, hitting api.market gateway, and calculating optimization windows..."):
            with open("temp_manifest.csv", "wb") as f:
                f.write(uploaded_file.getbuffer())

            date_str = target_date.strftime("%Y-%m-%d")

            try:
                # Settings are built fresh per run, from this session's sidebar values --
                # no shared globals between concurrent Streamlit sessions.
                settings = load_settings_from_env(arrival_iata=arrival_iata, manifest_date=date_str)
            except ValueError as e:
                st.error(str(e))
                st.stop()

            columns, rows = run_extraction_pipeline(settings, csv_path="temp_manifest.csv")
            optimized_rows = run_optimization_pipeline(rows, max_wait_hours=max_wait)
            output_columns = ["Pickup Group ID", "Target Vehicle Dispatch", "Passenger Wait Time"] + columns

        if optimized_rows:
            # Typed rows -> flat dicts -> DataFrame. No more list.insert() index juggling
            # or reconstructing [[header], [row1], ...] before handing off to pandas.
            df = pd.DataFrame([row.to_dict(output_columns) for row in optimized_rows], columns=output_columns)

            # Column headers should already be unique, but guard against duplicate
            # manifest headers (e.g. two blank/duplicate CSV columns) just in case.
            seen = {}
            unique_columns = []
            for col in df.columns:
                col_str = str(col) if col else "Blank"
                seen[col_str] = seen.get(col_str, 0) + 1
                unique_columns.append(col_str if seen[col_str] == 1 else f"{col_str}_{seen[col_str] - 1}")
            df.columns = unique_columns

            df = df.reset_index(drop=True)

            st.subheader("Schedule View")
            st.write(f"Total Rows Accounted For: **{len(df)}**")

            def highlight_issues(row):
                return ['background-color: #ffcccc' if 'MANUAL REVIEW' in str(val) or 'INVALID' in str(val) else '' for val in row]

            st.dataframe(df.style.apply(highlight_issues, axis=1), width="stretch")

            st.subheader("Export Schedule")
            col1, col2 = st.columns(2)

            with col1:
                csv_buffer = io.StringIO()
                df.to_csv(csv_buffer, index=False)
                st.download_button(
                    label="📥 Download Manifest CSV",
                    data=csv_buffer.getvalue().encode("utf-8"),
                    file_name=f"shuttle_manifest_{date_str}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

            with col2:
                with st.spinner("Compiling PDF document..."):
                    pdf_buffer = io.BytesIO()
                    success = save_pipeline_to_pdf(
                        rows=optimized_rows,
                        columns=output_columns,
                        output_pdf_path=pdf_buffer,
                        manifest_date=date_str,
                    )

                    if success:
                        st.download_button(
                            label="📄 Download Printable PDF Report",
                            data=pdf_buffer.getvalue(),
                            file_name=f"shuttle_manifest_{date_str}.pdf",
                            mime="application/pdf",
                            use_container_width=True,
                        )
                    else:
                        st.error("Could not compile PDF report. Check backend logs.")
        else:
            st.error("Pipeline failure. Check your console logs or verify the format structure of your CSV.")
