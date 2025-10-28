import streamlit as st
import pandas as pd
from fpdf import FPDF
from datetime import datetime
import qrcode
from io import BytesIO
from pathlib import Path

def qrcode_png_bytes(data: str, box_size=3, border=0):
    qr = qrcode.QRCode(box_size=box_size, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def _use_thai_font(pdf, size=10):
    try_paths = [
        Path("fonts/NotoSansThai-Regular.ttf"),
        Path("NotoSansThai-Regular.ttf"),
        Path("fonts/THSarabunNew.ttf"),
        Path("THSarabunNew.ttf"),
    ]
    ttf_path = next((p for p in try_paths if p.exists()), None)
    if ttf_path is None:
        raise FileNotFoundError("ไม่พบไฟล์ฟอนต์ไทย (เช่น fonts/NotoSansThai-Regular.ttf)")
    pdf.add_font("NotoThai", "", str(ttf_path), uni=True)
    pdf.set_font("NotoThai", size=size)

def build_labels_pdf_fpdf(rows: pd.DataFrame, label_w_mm=62, label_h_mm=29, margin_mm=5, cols=3, rows_per_page=8) -> bytes:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False, margin=0)

    try:
        _use_thai_font(pdf, size=10)
    except Exception as e:
        st.warning(f"ไม่พบฟอนต์ไทย: {e} → จะพิมพ์ด้วย Helvetica (อักษรไทยจะหาย)")
        pdf.set_font("Helvetica", size=10)

    col_w, row_h = label_w_mm, label_h_mm
    x0, y0 = margin_mm, margin_mm

    pdf.add_page()
    i = 0
    for _, r in rows.iterrows():
        col = i % cols
        row = (i // cols) % rows_per_page
        if i > 0 and row == 0 and col == 0:
            pdf.add_page()
        x = x0 + col*col_w
        y = y0 + row*row_h

        tag = str(r.get("asset_tag",""))
        name = str(r.get("name",""))[:28]
        branch = str(r.get("branch",""))

        pdf.set_font(pdf.font_family, style="", size=10)
        pdf.text(x+2, y+6, tag)

        pdf.set_font(pdf.font_family, size=9)
        pdf.text(x+2, y+11, name)

        pdf.set_font(pdf.font_family, size=8)
        pdf.text(x+2, y+row_h-3, branch)

        qr_bytes = qrcode_png_bytes(tag, box_size=3, border=0)
        qr_x = x + col_w - 22
        qr_y = y + (row_h - 20)/2
        pdf.image(stream=qr_bytes, type="PNG", x=qr_x, y=qr_y, w=20, h=20)

        i += 1

    return pdf.output(dest="S").encode("latin-1")

st.title("🧾 IT Asset Label Generator (Thai Font Ready)")

uploaded = st.file_uploader("อัปโหลด CSV ที่มีคอลัมน์ asset_tag, name, branch", type=["csv"])
if uploaded:
    df = pd.read_csv(uploaded)
    st.dataframe(df.head())

    if st.button("สร้าง PDF ป้ายฉลาก"):
        pdf_bytes = build_labels_pdf_fpdf(df)
        st.download_button("ดาวน์โหลด PDF", pdf_bytes, file_name="labels_thai.pdf", mime="application/pdf")


