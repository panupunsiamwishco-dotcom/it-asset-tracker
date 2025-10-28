
# -*- coding: utf-8 -*-
"""
IT Asset Tracker — Google Sheets + Login + Thai PDF Labels (fpdf2 patched)

ฟีเจอร์หลัก
- Login แบบง่ายผ่าน st.secrets [users]
- จัดเก็บข้อมูลลง Google Sheets (ผ่าน Service Account ใน st.secrets["gcp"] และ SHEET_ID)
- เพิ่ม/แก้ไข/ค้นหา/ลบ อุปกรณ์
- สร้าง Asset Tag อัตโนมัติ: <รหัสสาขา(เฉพาะตัวเลข 3 หลัก)><ปีแบบ yy><เลขรันนิ่ง N หลัก>
- พิมพ์แท็ก (PDF ภาษาไทย) รองรับฟอนต์ไทย (ค้นหาไฟล์ในโฟลเดอร์ fonts/)
- ช่อง Scan แบบคีย์บอร์ด (รองรับเครื่องสแกนบาร์โค้ด/QR แบบ HID)

วิธีตั้งค่า secrets.toml ตัวอย่าง:
[users.admin]
password = "1234"

[gcp]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "...@....iam.gserviceaccount.com"
token_uri = "https://oauth2.googleapis.com/token"

SHEET_ID = "ใส่ไอดีสเปรดชีต"

ต้องมี worksheet 2 แท็บ: assets, asset_history (ถ้ายังไม่มี ระบบจะสร้างหัวคอลัมน์ให้เอง)
"""
from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

import streamlit as st
import pandas as pd
from fpdf import FPDF
import qrcode

# ---------- Google Sheets (optional but recommended) ----------
def get_gs_client():
    try:
        from google.oauth2.service_account import Credentials
        import gspread
    except Exception:
        return None, None
    gcp = st.secrets.get("gcp", None)
    sheet_id = st.secrets.get("SHEET_ID", None)
    if not gcp or not isinstance(gcp, dict) or not sheet_id:
        return None, None
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(gcp, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheet_id)
        return gc, sh
    except Exception as e:
        st.sidebar.error(f"Google Sheets error: {e}")
        return None, None

ASSET_COLS = [
    "id","asset_tag","name","category","serial_no","vendor","purchase_date",
    "warranty_expiry","status","branch","location","assigned_to",
    "installed_date","notes","last_update"
]
HIS_COLS = ["ts","user","action","asset_tag","branch","note"]

def ensure_worksheets(sh):
    try:
        ws_assets = next((w for w in sh.worksheets() if w.title=="assets"), None)
        if ws_assets is None:
            ws_assets = sh.add_worksheet("assets", rows=2, cols=len(ASSET_COLS))
            ws_assets.update("A1", [ASSET_COLS])
        else:
            # make sure headers exist
            headers = ws_assets.row_values(1) or []
            if headers != ASSET_COLS:
                ws_assets.clear()
                ws_assets.update("A1", [ASSET_COLS])
        ws_his = next((w for w in sh.worksheets() if w.title=="asset_history"), None)
        if ws_his is None:
            ws_his = sh.add_worksheet("asset_history", rows=2, cols=len(HIS_COLS))
            ws_his.update("A1", [HIS_COLS])
    except Exception as e:
        st.error(f"สร้าง/ตรวจสอบ worksheet ไม่สำเร็จ: {e}")

def load_assets_df(sh) -> pd.DataFrame:
    try:
        ws = sh.worksheet("assets")
        rows = ws.get_all_records()
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(columns=ASSET_COLS)
        # ensure all columns
        for c in ASSET_COLS:
            if c not in df.columns:
                df[c] = ""
        return df[ASSET_COLS].copy()
    except Exception:
        return pd.DataFrame(columns=ASSET_COLS)

def save_assets_df(sh, df: pd.DataFrame):
    ws = sh.worksheet("assets")
    if df.empty:
        ws.clear()
        ws.update("A1", [ASSET_COLS])
        return
    ws.clear()
    ws.update("A1", [ASSET_COLS])
    ws.update(f"A2", df[ASSET_COLS].astype(str).values.tolist())

def append_history(sh, user:str, action:str, asset_tag:str, branch:str, note:str=""):
    try:
        ws = sh.worksheet("asset_history")
        ws.append_row([datetime.now().isoformat(timespec="seconds"), user, action, asset_tag, branch, note], table_range="A1")
    except Exception:
        pass

# ---------- Simple Auth ----------
def ensure_login() -> str:
    st.sidebar.header("เข้าสู่ระบบ")
    users = st.secrets.get("users", {})
    if not users:
        st.sidebar.info("""ยังไม่พบผู้ใช้ใน `secrets.toml → [users]`
รูปแบบ: `[users.<username>] password="..."`""")
        return "admin-demo"
    if "auth_user" in st.session_state and st.session_state.auth_user:
        user = st.session_state.auth_user
        st.sidebar.success(f"ผู้ใช้: {user}")
        if st.sidebar.button("ออกจากระบบ"):
            st.session_state.auth_user = None
        return user
    u = st.sidebar.text_input("ผู้ใช้")
    p = st.sidebar.text_input("รหัสผ่าน", type="password")
    if st.sidebar.button("Login", use_container_width=True):
        if u in users and str(users[u].get("password","")) == str(p):
            st.session_state.auth_user = u
            st.sidebar.success("เข้าสู่ระบบสำเร็จ")
            return u
        else:
            st.sidebar.error("ผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
    return ""

# ---------- Asset Tag generator ----------
def gen_asset_tag(branch_code:str, df_existing:pd.DataFrame, run_digits:int=5, year_mode:str="yy") -> str:
    # ใช้เฉพาะตัวเลขจาก branch_code
    digits = re.sub(r"\D", "", str(branch_code))[:3].rjust(3,"0")
    yy = datetime.now().strftime("%y") if year_mode=="yy" else (datetime.now().strftime("%Y") if year_mode=="yyyy" else "")
    # หาเลขรันสูงสุดที่มี prefix เดียวกัน
    prefix = f"{digits}{yy}"
    if "asset_tag" in df_existing.columns and not df_existing.empty:
        same = df_existing["asset_tag"].astype(str).str.startswith(prefix)
        if same.any():
            last_run = (
                df_existing.loc[same, "asset_tag"]
                .astype(str)
                .str.replace(prefix, "", regex=False)
                .str.replace(r"\D", "", regex=True)
                .replace("", "0")
                .astype(int)
                .max()
            )
        else:
            last_run = 0
    else:
        last_run = 0
    nxt = str(last_run + 1).rjust(run_digits, "0")
    return f"{prefix}{nxt}"

# ---------- PDF (Thai labels) with patched fpdf2 image() ----------
FONT_CANDIDATES = [
    Path("fonts/NotoSansThai-Regular.ttf"),
    Path("NotoSansThai-Regular.ttf"),
    Path("fonts/THSarabunNew.ttf"),
    Path("THSarabunNew.ttf"),
]

def _thai_font(pdf:FPDF, size:int=10):
    ttf = next((p for p in FONT_CANDIDATES if p.exists()), None)
    if ttf is None:
        st.warning("ไม่พบฟอนต์ไทย (เช่น fonts/NotoSansThai-Regular.ttf) → จะใช้ Helvetica (ข้อความไทยจะไม่แสดง)")
        pdf.set_font("Helvetica", size=size)
        return
    pdf.add_font("TH", "", str(ttf), uni=True)
    pdf.set_font("TH", size=size)

def _qr_png_bytes(data:str, box_size:int=3, border:int=1) -> BytesIO:
    q = qrcode.QRCode(box_size=box_size, border=border)
    q.add_data(str(data))
    q.make(fit=True)
    img = q.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio

def build_labels_pdf_fpdf(df:pd.DataFrame, label_w_mm:float=62, label_h_mm:float=29, cols:int=3, rows_per_page:int=8, margin_mm:float=5) -> bytes:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.add_page()
    _thai_font(pdf, size=10)

    col_w, row_h = label_w_mm, label_h_mm
    x0, y0 = margin_mm, margin_mm

    i = 0
    for _, r in df.iterrows():
        col = i % cols
        row = (i // cols) % rows_per_page
        if i > 0 and row == 0 and col == 0:
            pdf.add_page()
        x = x0 + col*col_w
        y = y0 + row*row_h

        tag = str(r.get("asset_tag",""))
        name = str(r.get("name",""))
        branch = str(r.get("branch",""))

        # กรอบ
        pdf.set_draw_color(170,170,170)
        pdf.rect(x, y, col_w, row_h)

        # ข้อความ
        pdf.set_xy(x+2, y+3)
        pdf.multi_cell(col_w-24, 5, f"{tag}\n{name}\n{branch}", 0)

        # QR (patched: ส่ง BytesIO เป็นอาร์กิวเมนต์ตัวแรก ไม่ใช้ stream=)
        qr_bytes = _qr_png_bytes(tag or name, box_size=3, border=0)
        qr_size = min(22, row_h-6)
        qr_x = x + col_w - (qr_size + 2)
        qr_y = y + (row_h - qr_size)/2
        pdf.image(qr_bytes, x=qr_x, y=qr_y, w=qr_size, h=qr_size, type="PNG")

        i += 1

    return pdf.output(dest="S").encode("latin-1")

# ---------- UI ----------
st.set_page_config(page_title="IT Asset Tracker (Sheets + Thai PDF)", layout="wide")
st.title("💻 IT Asset Tracker (Google Sheets + Login + Mobile Scan + Thai PDF)")

user = ensure_login()
if not user:
    st.stop()

gc, sh = get_gs_client()
if sh:
    ensure_worksheets(sh)
    st.sidebar.success("เชื่อมต่อ Google Sheets แล้ว")
else:
    st.sidebar.warning("ยังไม่ได้เชื่อมต่อ Google Sheets — จะทำงานแบบชั่วคราวในหน้านี้เท่านั้น")

if "local_df" not in st.session_state:
    st.session_state.local_df = pd.DataFrame(columns=ASSET_COLS)

menu = st.sidebar.radio("เมนู", [
    "แดชบอร์ด","เพิ่ม/แก้ไข อุปกรณ์","ค้นหา + อัปเดต","พิมพ์แท็ก (PDF ไทย)","ประวัติการเปลี่ยนแปลง","สแกน (คีย์บอร์ด)",
])

# ----- Dashboard -----
if menu == "แดชบอร์ด":
    df = load_assets_df(sh) if sh else st.session_state.local_df
    c1,c2,c3,c4 = st.columns(4)
    total = len(df)
    installed = (df["status"].astype(str).str.contains("ติดตั้ง")).sum() if not df.empty else 0
    in_stock = (df["status"].astype(str).str.contains("พร้อมใช้")).sum() if not df.empty else 0
    changed = (df["status"].astype(str).str.contains("ซ่อม|เปลี่ยน")).sum() if not df.empty else 0
    c1.metric("ทั้งหมด", total); c2.metric("ติดตั้งแล้ว", installed); c3.metric("พร้อมใช้งาน", in_stock); c4.metric("ซ่อม/เปลี่ยน", changed)
    st.dataframe(df, use_container_width=True)

# ----- Add / Edit -----
elif menu == "เพิ่ม/แก้ไข อุปกรณ์":
    df = load_assets_df(sh) if sh else st.session_state.local_df
    st.subheader("เพิ่ม/แก้ไข")

    col = st.columns(3)
    name = col[0].text_input("ชื่ออุปกรณ์")
    branch = col[1].text_input("สาขา (เช่น SWC001)")
    category = col[2].text_input("หมวดหมู่")
    serial_no = st.text_input("Serial No.")
    vendor = st.text_input("Vendor")
    purchase_date = st.date_input("วันที่ซื้อ", value=None, format="YYYY-MM-DD")
    warranty_expiry = st.date_input("หมดประกัน", value=None, format="YYYY-MM-DD")
    status = st.selectbox("สถานะ", ["พร้อมใช้งาน","ติดตั้งแล้ว","ซ่อม/เปลี่ยน"])
    location = st.text_input("ตำแหน่งติดตั้ง/เก็บ")
    assigned_to = st.text_input("ผู้รับผิดชอบ/ผู้ใช้งาน")
    installed_date = st.date_input("วันที่ติดตั้ง", value=None, format="YYYY-MM-DD")
    notes = st.text_area("หมายเหตุ")
    auto = st.checkbox("สร้าง Asset Tag อัตโนมัติ (เลขล้วนจากรหัสสาขา)", value=True)
    run_digits = st.number_input("จำนวนหลักของเลขรัน", 3, 8, 5)
    year_mode = st.selectbox("รูปแบบปีในรหัส", ["yy","yyyy","ไม่ใส่"])

    # กำหนด tag
    if auto:
        tag = gen_asset_tag(branch, df, run_digits, year_mode if year_mode!="ไม่ใส่" else "")
        st.text_input("Asset Tag (อัตโนมัติ)", tag, disabled=True)
    else:
        tag = st.text_input("Asset Tag (กำหนดเอง)")

    if st.button("บันทึก/เพิ่ม"):
        new_row = {
            "id": str(datetime.now().timestamp()).split(".")[0],
            "asset_tag": tag,
            "name": name,
            "category": category,
            "serial_no": serial_no,
            "vendor": vendor,
            "purchase_date": str(purchase_date) if purchase_date else "",
            "warranty_expiry": str(warranty_expiry) if warranty_expiry else "",
            "status": status,
            "branch": branch,
            "location": location,
            "assigned_to": assigned_to,
            "installed_date": str(installed_date) if installed_date else "",
            "notes": notes,
            "last_update": datetime.now().isoformat(timespec="seconds"),
        }
        if sh:
            df = load_assets_df(sh)
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            save_assets_df(sh, df)
            append_history(sh, user, "ADD", tag, branch, name)
        else:
            st.session_state.local_df = pd.concat([st.session_state.local_df, pd.DataFrame([new_row])], ignore_index=True)
        st.success(f"บันทึก {tag} เรียบร้อย")

# ----- Search + Update -----
elif menu == "ค้นหา + อัปเดต":
    df = load_assets_df(sh) if sh else st.session_state.local_df
    q = st.text_input("ค้นหา (asset_tag / name / branch)")
    if q:
        m = df.apply(lambda r: q.lower() in str(r["asset_tag"]).lower() or q.lower() in str(r["name"]).lower() or q.lower() in str(r["branch"]).lower(), axis=1)
        sdf = df[m].copy()
    else:
        sdf = df.copy()
    st.dataframe(sdf, use_container_width=True)
    st.caption("เลือกหนึ่งรายการเพื่อแก้ไข/ลบ")
    if not sdf.empty:
        idx = st.number_input("ลำดับแถว (index) ที่ต้องการแก้/ลบ", 0, len(sdf)-1, 0)
        row = sdf.iloc[int(idx)].to_dict()
        st.write(row)
        c1,c2 = st.columns(2)
        if c1.button("ลบรายการนี้"):
            df = df[df["asset_tag"] != row["asset_tag"]].copy()
            if sh: save_assets_df(sh, df); append_history(sh, user, "DEL", row["asset_tag"], row["branch"], row.get("name",""))
            else: st.session_state.local_df = df
            st.success("ลบแล้ว")
        if c2.button("อัปเดตเวลา last_update"):
            df.loc[df["asset_tag"]==row["asset_tag"], "last_update"] = datetime.now().isoformat(timespec="seconds")
            if sh: save_assets_df(sh, df); append_history(sh, user, "UPDATE", row["asset_tag"], row["branch"], "touch")
            else: st.session_state.local_df = df
            st.success("อัปเดตแล้ว")

# ----- PDF labels -----
elif menu == "พิมพ์แท็ก (PDF ไทย)":
    df = load_assets_df(sh) if sh else st.session_state.local_df
    st.info("เลือกแถวที่ต้องการพิมพ์ แล้วกดสร้าง PDF")
    if not df.empty:
        chosen = st.multiselect("เลือก asset_tag", df["asset_tag"].tolist())
        subset = df[df["asset_tag"].isin(chosen)] if chosen else df.head(24)
        c = st.columns(4)
        w = c[0].number_input("กว้าง (มม.)", 40, 100, 62)
        h = c[1].number_input("สูง (มม.)", 15, 60, 29)
        cols = c[2].number_input("คอลัมน์/หน้า", 1, 6, 3)
        rows = c[3].number_input("แถว/หน้า", 1, 12, 8)
        if st.button("สร้าง PDF", type="primary"):
            pdf_bytes = build_labels_pdf_fpdf(subset, w, h, int(cols), int(rows))
            st.download_button("ดาวน์โหลด PDF", data=pdf_bytes, file_name="asset_labels_thai.pdf", mime="application/pdf")
    else:
        st.warning("ยังไม่มีข้อมูล")

# ----- History -----
elif menu == "ประวัติการเปลี่ยนแปลง":
    if sh:
        try:
            ws = sh.worksheet("asset_history")
            rows = ws.get_all_records()
            hist = pd.DataFrame(rows)
        except Exception:
            hist = pd.DataFrame(columns=HIS_COLS)
        st.dataframe(hist, use_container_width=True)
    else:
        st.info("ฟังก์ชันนี้ใช้ได้เมื่อเชื่อมต่อ Google Sheets")

# ----- Scan (keyboard) -----
elif menu == "สแกน (คีย์บอร์ด)":
    st.info("นำเครื่องสแกนบาร์โค้ด/QR (แบบคีย์บอร์ด) มาจ่อแล้วยิงได้เลย")
    code = st.text_input("Scan / พิมพ์ Asset Tag แล้วกด Enter")
    if code:
        df = load_assets_df(sh) if sh else st.session_state.local_df
        found = df[df["asset_tag"].astype(str)==str(code)]
        if found.empty:
            st.error("ไม่พบรหัสนี้")
        else:
            st.success("พบรายการ:")
            st.dataframe(found, use_container_width=True)

