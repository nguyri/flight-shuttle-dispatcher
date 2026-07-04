import os
import requests
import json
from datetime import datetime
import pandas as pd
import streamlit as st
from pathlib import Path

def send_bilingual_sms(phone: str, pickup_time: str) -> tuple[bool, str]:
    """
    Python replacement for the Google Apps Script Twilio workflow.
    Returns (success_flag, display_status_message)
    """
    account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
    api_key_sid = os.environ.get('TWILIO_API_KEY_SID') or os.environ.get('TWILIO_AUTH_TOKEN')
    api_key_secret = os.environ.get('TWILIO_API_KEY_SECRET')
    from_number = os.environ.get('TWILIO_FROM')

    if not phone:
        return False, "SKIPPED: No Phone"
    if not account_sid or not from_number:
        return False, "FAILED: Config Missing"

    pickup_time = str(pickup_time).strip()

    message_en = f"""【Airport Pick-up Information】
Dear Guest,
Welcome to Calgary! My name is Dee, and I will be your airport pick-up tour guide.
📍 Pick up Location & Time
I will be waiting at Gate 11 at the arrival level at {pickup_time}, subject to weather and traffic delays. 
👋 How to Find Me
I will be wearing a Westar Travel uniform and a name board displaying “Dee”
🚌 Important Information
Due to airport regulations and the number of guests we need to pick up, our coach is only permitted to stop for 15 minutes at most and cannot remain at the curb beyond that time.
If you miss the bus, I will either return to pick you up as soon as possible or help arrange a hotel shuttle. If your flight schedule or estimated clearing time changes, please let me know as soon as possible so I can adjust the pickup schedule accordingly.
✅ Please reply to this message to confirm you have received it.
Thank you, and I look forward to meeting you soon!"""

    message_zh = f"""亲爱的贵宾您好，
欢迎来到卡尔加里！我是您此次接机导游小荻
📍 集合地点
请抵达后前往机场到达层（Arrivals Level）11号门等候。我将在 【{pickup_time}】于11号门外等您。若天气或突发交通影响可能会迟10-20分钟
👋 如何找到我
我会身穿 西星导游制服，并手持写有 “Dee” 的接机牌。
🚌 温馨提醒
由于机场管理规定及当天接机旅客较多，旅游巴士只能短暂停靠，无法长时间等待。
如果您未能赶上接机巴士，请不用担心。我会根据实际情况，尽快返回机场接您，或协助您安排酒店接驳车。如果您的航班有变更，或以及通关时间有变，请尽快通知我，以便我及时调整接机安排。
✅ 收到本短信后，请回复确认已收到，谢谢！
期待很快与您见面！"""

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    auth = (api_key_sid, api_key_secret) if api_key_secret else (account_sid, api_key_sid)

    try:
        res_en = requests.post(url, data={"To": phone, "From": from_number, "Body": message_en}, auth=auth, timeout=10)
        res_zh = requests.post(url, data={"To": phone, "From": from_number, "Body": message_zh}, auth=auth, timeout=10)
        
        if res_en.status_code == 201 and res_zh.status_code == 201:
            return True, f"Sent {datetime.now().strftime('%H:%M')}"
        return False, f"FAILED: EN:{res_en.status_code} | ZH:{res_zh.status_code}"
    except Exception as e:
        return False, f"ERROR: {str(e)}"


def _save_dataframe_to_json_cache(df: pd.DataFrame, cache_save_path: Path):
    """Internal helper to convert dataframe back into the required structured key-value mapping."""
    if cache_save_path and cache_save_path.suffix == '.json':
        updated_records = df.to_dict(orient="records")
        updated_cache = {}
        for idx, rec in enumerate(updated_records):
            p_name = str(rec.get("Name", f"Passenger_{idx}")).replace(" ", "")
            flt_code = rec.get("Flight Code", "N/A")
            # Build unique compound lookup identifier key
            p_cache_key = f"{p_name}_{flt_code}_{idx}"
            updated_cache[p_cache_key] = rec
            
        with open(cache_save_path, "w", encoding="utf-8") as f:
            json.dump(updated_cache, f, indent=4, ensure_ascii=False)


def render_interactive_passenger_table(df: pd.DataFrame, cache_save_path: Path = None, cache_key_prefix: str = "") -> pd.DataFrame:
    """
    A single reusable function to display passenger records with built-in status indicators,
    real-time inline data editing cache updates, and Twilio dispatch controls.
    """
    # Standardize and seed required operational columns if missing
    if "SMS Status" not in df.columns:
        df["SMS Status"] = "Not Sent"
    if "Send SMS" not in df.columns:
        df["Send SMS"] = False

    # dispatch time is in string for editting
    if "Dispatch Time" in df.columns:
        df["Dispatch Time"] = df["Dispatch Time"].astype(str)

    # Force configurations and enable column-level mutability controls
    edited_df = st.data_editor(
        df,
        column_config={
            "Send SMS": st.column_config.CheckboxColumn(
                "Trigger SMS?",
                help="Check this row and click 'Execute Checked Dispatches' below to send out Twilio alerts.",
                default=False,
                width="small",
            ),
            "SMS Status": st.column_config.TextColumn(
                "SMS Status Indicator",
                disabled=True, # Read-only progress status tracker
                width="medium",
            ),
            "Name": st.column_config.TextColumn("Passenger Name", disabled=True, width="small"),
            "Contact Info": st.column_config.TextColumn("Contact Info", disabled=False, width="small"),     # Enabled Editing!
            "Flight Code": st.column_config.TextColumn("Flight Code", disabled=True, width="small"),
            "Dispatch Time": st.column_config.TextColumn("Dispatch Time", disabled=False, width="medium"), # Enabled Editing!
            "Group ID": st.column_config.TextColumn(" Group ID", disabled=True, width="small"),
            "Arrival Time": st.column_config.TextColumn("Arrival Time", disabled=True),
        },
        width='stretch',
        hide_index=True,
        key=f"editor_instance_{cache_key_prefix or hash(str(df.shape))}"
    )

    # --- Real-time Auto-save Engine ---
    # Compare whether structural parameters changed compared to the initialized frame 
    if cache_save_path and not edited_df.equals(df):
        # Filter out changes caused solely by selecting checkboxes to avoid unnecessary rewrites
        checkboxes_changed_only = edited_df.drop(columns=["Send SMS"]).equals(df.drop(columns=["Send SMS"])) if "Send SMS" in df.columns else False
        
        if not checkboxes_changed_only:
            _save_dataframe_to_json_cache(edited_df, cache_save_path)
            st.toast("💾 Changes auto-saved to cache registry!", icon="📝")

    # Action dispatch trigger block execution matching checked inputs
    if st.button("✉️ Send Checked SMS", key=f"btn_{cache_key_prefix or hash(str(cache_save_path))}"):
        records_updated = False
        
        for idx, row in edited_df.iterrows():
            if row["Send SMS"] is True:
                phone = row.get("Contact Info")
                pickup_time = row.get("Dispatch Time") or "Scheduled Time"
                
                if not phone or str(phone).strip() in ("N/A", ""):
                    edited_df.at[idx, "SMS Status"] = "SKIPPED: Missing Phone"
                    edited_df.at[idx, "Send SMS"] = False
                    records_updated = True
                    continue

                with st.spinner(f"Notifying {row.get('Name', 'Passenger')}..."):
                    success, status_text = send_bilingual_sms(str(phone), str(pickup_time))
                
                # Turn off selection state and track results tracking strings
                edited_df.at[idx, "SMS Status"] = status_text
                edited_df.at[idx, "Send SMS"] = False
                records_updated = True

        if records_updated:
            st.success("Notifications successfully processed!")
            if cache_save_path:
                _save_dataframe_to_json_cache(edited_df, cache_save_path)
            st.rerun()
            
    return edited_df