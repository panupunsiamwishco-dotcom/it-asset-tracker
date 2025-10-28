
# app_patched_pdf_fpdf2.py
import io
from io import BytesIO
from pathlib import Path
from typing import List, Dict, Any

import streamlit as st
import pandas as pd
from fpdf import FPDF
import qrcode

APP_TITLE = "🖨️ IT Asset Label Generator (Thai PDF Ready, fpdf2 patched)"
FONT_CANDIDATES = [
    Path("fonts/NotoSansThai-Regular.ttf"),
    Path("NotoSansThai-Regular.ttf"),
    Path("fonts/THSarabunNew.ttf"),
    Path("THSarabunNew.ttf"),
]

def get_thai_font_path():
    for p in FONT_CANDIDATES:
        if p.exists():
            return p
    return None

def ensure_login() -> bool:
    st.sidebar.header("เข้าสู่ระบบ (Simple Auth)")
    users = st.secrets.get("users", None)
    if users is None or not isinstance(users, dict) or len(users) == 0:
        st.sidebar.info("""ยังไม่พบผู้ใช้ใน `secrets.toml → [users]`
รูปแบบ: `[users.<username>] password="..."`""")

        return True

    if "auth_user" not in st.session_state:
        st.session_state.auth_user = None

    if st.session_state.auth_user:
        st.sidebar.success(f"เข้าสู่ระบบเป็น: {st.session_state.auth_user}")
        if st.sidebar.button("ออกจากระบบ"):
            st.session_state.auth_user = None
        return True

    username = st.sidebar.text_input("ผู้ใช้", key="login_user")
    password = st.sidebar.text_input("รหัสผ่าน", type="password", key="login_pass")
    if st.sidebar.button("Login", use_container_width=True):
        if username in users and str(users[username].get("password","")) == str(password):
            st.session_state.auth_user = username
            st.sidebar.success("เข้าสู่ระบบสำเร็จ")
            return True
        else:
            st.sidebar.error("ผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
            return False

    return False

def make_qr_png_bytes(text: str) -> BytesIO:
    qr = qrcode.QRCode(version=2, box_size=6, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio

def build_labels_pdf_fpdf(df: pd.DataFrame, rows_per_page: int, cols_per_page: int) -> bytes:
    PAGE_W, PAGE_H = 210, 297
    margin = 8
    grid_w = (PAGE_W - margin * 2) / cols_per_page
    grid_h = (PAGE_H - margin * 2) / rows_per_page

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(False)

    font_path = get_thai_font_path()
    if font_path is None:
        st.toast("ไม่พบฟอนต์ไทย: จะแสดงด้วย Helvetica (ข้อความไทยอาจไม่ครบ)")
        pdf.add_page()
        pdf.set_font("Helvetica", size=10)
    else:
        pdf.add_page()
        pdf.add_font("TH", "", str(font_path), uni=True)
        pdf.set_font("TH", size=11)

    for i, row in df.iterrows():
        cell_index = i % (rows_per_page * cols_per_page)
        r = cell_index // cols_per_page
        c = cell_index % cols_per_page

        if cell_index == 0 and i != 0:
            pdf.add_page()

        x0 = margin + c * grid_w
        y0 = margin + r * grid_h

        pdf.set_draw_color(150,150,150)
        pdf.rect(x0, y0, grid_w, grid_h)

        asset_tag = str(row.get("asset_tag","")).strip()
        name = str(row.get("name","")).strip()
        branch = str(row.get("branch","")).strip()

        pdf.set_xy(x0 + 2, y0 + 2)
        pdf.multi_cell(w=grid_w - 24, h=5, txt=f"{asset_tag}\n{name}\nสาขา: {branch}", border=0)

        qr_size = min(22, grid_h - 6)
        qr_x = x0 + grid_w - (qr_size + 3)
        qr_y = y0 + 3

        qr_bytes = make_qr_png_bytes(asset_tag if asset_tag else name)
        # PATCH: pass BytesIO as positional arg, not 'stream='
        pdf.image(qr_bytes, x=qr_x, y=qr_y, w=qr_size, h=qr_size, type="PNG")

    out = pdf.output(dest="S").encode("latin-1")
    return out

def demo_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"asset_tag":"0012500001", "name":"เราเตอร์ TP-Link ER605", "branch":"SWC001"},
        {"asset_tag":"0012500002", "name":"สวิตช์ 24 พอร์ต", "branch":"SWC001"},
        {"asset_tag":"0022500001", "name":"กล้อง VIGI NVR", "branch":"SWC002"},
        {"asset_tag":"0022500002", "name":"AP EAP115 V4", "branch":"SWC002"},
    ])

st.set_page_config(page_title="IT Asset Labels (fpdf2 patch)", layout="wide")
st.title(APP_TITLE)

if not ensure_login():
    st.stop()

st.subheader("อัปโหลดข้อมูลสินทรัพย์")
src = st.radio("แหล่งข้อมูล", ["อัปโหลด CSV", "ตัวอย่างทดสอบ"], horizontal=True)

df = None
if src == "อัปโหลด CSV":
    up = st.file_uploader("เลือกไฟล์ CSV ที่มีคอลัมน์: asset_tag, name, branch", type=["csv"])
    if up:
        df = pd.read_csv(up)
else:
    df = demo_df()
    st.caption("ใช้ข้อมูลตัวอย่างสำหรับทดสอบ")

if df is not None and len(df) > 0:
    st.dataframe(df, use_container_width=True)

    st.markdown("---")
    st.subheader("ตั้งค่าแผ่น A4 / จำนวนช่อง")
    cols = st.columns(3)
    rows_per_page = cols[0].number_input("จำนวนแถวต่อหน้า", min_value=1, max_value=12, value=8, step=1)
    cols_per_page = cols[1].number_input("จำนวนคอลัมน์ต่อหน้า", min_value=1, max_value=6, value=3, step=1)
    _ = cols[2].markdown("A4, margin 8mm, ขนาดช่องคำนวณอัตโนมัติ")

    if st.button("สร้าง PDF", type="primary"):
        try:
            pdf_bytes = build_labels_pdf_fpdf(df, rows_per_page, cols_per_page)
            st.download_button("ดาวน์โหลด PDF ป้ายทรัพย์สิน", data=pdf_bytes, file_name="asset_labels_thai.pdf", mime="application/pdf", use_container_width=True)
            st.success("สร้าง PDF เสร็จแล้ว ✅")
        except Exception as e:
            st.exception(e)
else:
    st.info("อัปโหลดไฟล์ CSV หรือเลือกโหมดตัวอย่างเพื่อเริ่มสร้างป้าย")
