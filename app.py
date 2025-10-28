# -*- coding: utf-8 -*-
import time
import streamlit as st
import pandas as pd
try:
    import gspread
except Exception:
    gspread = None

st.set_page_config(page_title="IT Asset Tracker (Sheets Patched)", page_icon="🧾", layout="wide")

def get_sheet_id() -> str:
    sid = st.secrets.get("SHEET_ID")
    if not sid:
        sid = (st.secrets.get("gcp", {}) or {}).get("SHEET_ID")
    if not sid:
        sid = st.secrets.get("sheet_id")
    if not sid:
        with st.sidebar.expander("ตั้งค่า SHEET_ID (ชั่วคราวในหน้านี้)"):
            tmp = st.text_input("SHEET_ID", value=st.session_state.get("SHEET_ID", ""))
            if st.button("ใช้ SHEET_ID นี้"):
                st.session_state["SHEET_ID"] = tmp
                st.success("ตั้งค่า SHEET_ID ชั่วคราวแล้ว")
        sid = st.session_state.get("SHEET_ID", "")
    return sid

def get_gs_client():
    if gspread is None:
        st.error("ยังไม่ได้ติดตั้ง gspread (เพิ่มลง requirements.txt)")
        return None
    gcp = st.secrets.get("gcp", None)
    if not gcp:
        st.error("ไม่พบ [gcp] ใน secrets.toml")
        return None
    try:
        gc = gspread.service_account_from_dict(dict(gcp))
        return gc
    except Exception as e:
        st.error(f"สร้าง client ไม่สำเร็จ: {e}")
        return None

def login_simple() -> bool:
    users = st.secrets.get("users", {})
    if not users:
        with st.sidebar:
            st.info("ยังไม่พบ [users] ใน secrets.toml — เปิดใช้งานแบบไม่ล็อกอินชั่วคราว")
        return True
    if "auth_ok" not in st.session_state: st.session_state["auth_ok"] = False
    with st.sidebar:
        st.subheader("เข้าสู่ระบบ")
        u = st.text_input("ผู้ใช้", value="admin", key="u")
        p = st.text_input("รหัสผ่าน", type="password", value="1234", key="p")
        if st.button("Login"):
            user_def = users.get(u, {})
            if user_def and str(user_def.get("password", "")) == p:
                st.session_state["auth_ok"] = True
                st.success("เข้าสู่ระบบสำเร็จ")
                time.sleep(0.5)
                st.experimental_rerun()
            else:
                st.error("ผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
        if st.session_state["auth_ok"]:
            st.success("เข้าสู่ระบบแล้ว")
    return st.session_state["auth_ok"]

def read_assets(gc, sheet_id: str) -> pd.DataFrame:
    try:
        sh = gc.open_by_key(sheet_id)
        ws = sh.get_worksheet(0)
        records = ws.get_all_records()
        df = pd.DataFrame(records)
        cols = ["id","asset_tag","name","category","serial_no","vendor","purchase_date",
                "warranty_expiry","status","branch","location","assigned_to","installed_date",
                "notes","last_update"]
        for c in cols:
            if c not in df.columns: df[c] = ""
        if "id" in df.columns:
            with pd.option_context("mode.chained_assignment", None):
                df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
        return df[cols]
    except Exception as e:
        st.error(f"อ่าน Google Sheet ไม่สำเร็จ: {e}")
        return pd.DataFrame(columns=["id","asset_tag","name","category","serial_no","vendor",
                                     "purchase_date","warranty_expiry","status","branch","location",
                                     "assigned_to","installed_date","notes","last_update"])

def write_assets(gc, sheet_id: str, df: pd.DataFrame) -> bool:
    try:
        sh = gc.open_by_key(sheet_id)
        ws = sh.get_worksheet(0)
        ws.clear()
        ws.update([df.columns.tolist()] + df.values.tolist())
        return True
    except Exception as e:
        st.error(f"บันทึกกลับไปยัง Google Sheet ไม่สำเร็จ: {e}")
        return False

st.title("💻 IT Asset Tracker (Google Sheets + Login) — Patched")

with st.sidebar.expander("Debug: ดูคีย์ใน secrets"):
    try:
        st.write("Top-level keys:", list(st.secrets.keys()))
        st.write("gcp keys:", list((st.secrets.get("gcp", {}) or {}).keys()))
    except Exception:
        st.write("st.secrets อ่านไม่ได้ในโหมด local")

auth_ok = login_simple()
SHEET_ID = get_sheet_id()
if not SHEET_ID: st.warning("ยังไม่ได้ตั้งค่า SHEET_ID")
else: st.info(f"SHEET_ID: `{SHEET_ID}`")

if not auth_ok: st.stop()
gc = get_gs_client() if SHEET_ID else None
if SHEET_ID and gc: df = read_assets(gc, SHEET_ID)
else:
    df = pd.DataFrame(columns=["id","asset_tag","name","category","serial_no","vendor",
                               "purchase_date","warranty_expiry","status","branch","location",
                               "assigned_to","installed_date","notes","last_update"])

c1, c2, c3 = st.columns(3)
with c1: st.metric("ทั้งหมด", len(df))
with c2: st.metric("พร้อมใช้งาน", int((df["status"]=="in_stock").sum()) if "status" in df else 0)
with c3: st.metric("ซ่อม/เปลี่ยน", int((df["status"]=="repair").sum()) if "status" in df else 0)
st.divider()
st.subheader("ตารางทรัพย์สิน")
st.dataframe(df, use_container_width=True)
st.divider()
with st.expander("เพิ่มตัวอย่างแถว (เขียนลงชีต)"):
    sample = {
        "id": int(df["id"].max()+1 if "id" in df and len(df) else 1),
        "asset_tag": "IT-TESTSWC001-0001","name":"อุปกรณ์ทดสอบ","category":"Other",
        "serial_no":"SN123456","vendor":"vendor-x","purchase_date": time.strftime("%Y-%m-%d"),
        "warranty_expiry":"","status":"in_stock","branch":"SWC001","location":"คลัง",
        "assigned_to":"","installed_date":"","notes":"เพิ่มโดยปุ่มตัวอย่าง",
        "last_update": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    st.write(sample)
    if st.button("เขียนแถวตัวอย่าง"):
        if SHEET_ID and gc:
            new_df = pd.concat([df, pd.DataFrame([sample])], ignore_index=True)
            ok = write_assets(gc, SHEET_ID, new_df)
            if ok: st.success("เขียนข้อมูลลงชีตแล้ว!")
        else: st.error("ยังไม่ได้เชื่อมต่อ Google Sheets")
