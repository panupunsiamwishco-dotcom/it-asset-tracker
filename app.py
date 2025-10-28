
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from datetime import datetime, date
from io import BytesIO
from pathlib import Path
import re
import qrcode

# Google Sheets
import gspread
from google.oauth2.service_account import Credentials

# PDF
from fpdf import FPDF

# -------------------------- APP CONFIG --------------------------
st.set_page_config(page_title="IT Asset Tracker (GSheets + Login + Scan + Thai PDF)", page_icon="🖥️", layout="wide")

SHEET_ID = st.secrets.get("SHEET_ID", "")
GCP_INFO = dict(st.secrets.get("gcp", {}))

ASSET_HEADERS = ["id","asset_tag","name","category","serial_no","vendor","purchase_date","warranty_expiry","status","branch","location","assigned_to","installed_date","notes","last_update"]
HIST_HEADERS  = ["asset_id","asset_tag","action","details","user","branch","ts"]

# ---------------------------- AUTH (Simple) ----------------------------
import hashlib
def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def do_login():
    users = st.secrets.get("users", {})
    if not users:
        st.sidebar.error("ยังไม่พบผู้ใช้ใน secrets.toml → [users.<username>.*]")
        st.stop()

    st.sidebar.header("เข้าสู่ระบบ")
    u = st.sidebar.text_input("ผู้ใช้", key="u")
    p = st.sidebar.text_input("รหัสผ่าน", type="password", key="p")
    submit = st.sidebar.button("Login")

    if "auth_ok" not in st.session_state:
        st.session_state["auth_ok"] = False

    if submit:
        rec = users.get(u)
        ok = False
        if rec:
            if "password_plain" in rec and p == rec.get("password_plain",""):
                ok = True
            elif "password_sha256" in rec and sha256_hex(p) == rec.get("password_sha256","").lower():
                ok = True
        if ok:
            st.session_state["auth_ok"] = True
            st.session_state["current_user"] = u
            st.sidebar.success(f"ยินดีต้อนรับ {u}")
        else:
            st.sidebar.error("ผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")

    if st.session_state.get("auth_ok"):
        if st.sidebar.button("ออกจากระบบ"):
            for k in ["auth_ok","current_user","u","p"]:
                st.session_state.pop(k, None)
            st.experimental_rerun()
        return True
    else:
        st.stop()

# ------------------------- GOOGLE SHEETS I/O -------------------------
def get_gs_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(GCP_INFO, scopes=scopes)
    return gspread.authorize(creds)

def ensure_sheets(gc):
    if not SHEET_ID:
        st.error("ยังไม่ได้ตั้งค่า SHEET_ID ใน secrets.toml")
        st.stop()
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws_assets = sh.worksheet("assets")
    except gspread.WorksheetNotFound:
        ws_assets = sh.add_worksheet("assets", rows=1000, cols=20)
        ws_assets.update("A1:O1", [ASSET_HEADERS])
    try:
        ws_hist = sh.worksheet("asset_history")
    except gspread.WorksheetNotFound:
        ws_hist = sh.add_worksheet("asset_history", rows=1000, cols=10)
        ws_hist.update("A1:G1", [HIST_HEADERS])
    return sh, ws_assets, ws_hist

def read_assets_df(ws_assets):
    vals = ws_assets.get_all_values()
    if not vals:
        return pd.DataFrame(columns=ASSET_HEADERS)
    df = pd.DataFrame(vals[1:], columns=vals[0])
    if df.empty:
        df = pd.DataFrame(columns=ASSET_HEADERS)
    if "id" in df.columns:
        df["id"] = pd.to_numeric(df["id"], errors="coerce").astype("Int64")
    return df

def write_assets_df(ws_assets, df):
    df2 = df.reindex(columns=ASSET_HEADERS)
    out = [ASSET_HEADERS] + df2.fillna("").astype(str).values.tolist()
    ws_assets.clear()
    ws_assets.update(f"A1:O{len(out)}", out)

def append_history(ws_hist, row: list):
    ws_hist.append_row(row, value_input_option="USER_ENTERED")

# ------------------------ TAG GENERATION (Numeric) ------------------------
def _digits(s: str) -> str:
    m = re.findall(r"\d+", str(s))
    return "".join(m) if m else "000"

def gen_next_tag_numeric(
    df: pd.DataFrame,
    branch_code: str,
    year_mode: str = "yy",
    seq_len: int = 5
) -> str:
    br = _digits(branch_code).zfill(3)
    if year_mode == "yy":
        yr = datetime.now().strftime("%y")
    elif year_mode == "yyyy":
        yr = datetime.now().strftime("%Y")
    else:
        yr = ""
    prefix = f"{br}{yr}"
    seq_max = 0
    if not df.empty and "asset_tag" in df.columns:
        cand = df["asset_tag"].fillna("").astype(str)
        for t in cand:
            if t.isdigit() and t.startswith(prefix):
                tail = t[len(prefix):]
                if tail.isdigit():
                    seq_max = max(seq_max, int(tail))
    next_seq = str(seq_max + 1).zfill(seq_len)
    return f"{prefix}{next_seq}"

def gen_next_id(df):
    if df.empty or df["id"].isna().all():
        return 1
    return int(df["id"].max()) + 1

# ----------------------------- QR Helper -----------------------------
def qrcode_png_bytes(data: str, box_size=3, border=0):
    qr = qrcode.QRCode(box_size=box_size, border=border)
    qr.add_data(str(data))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ------------------------ fpdf2 + Thai Font Support -----------------------
def _use_thai_font(pdf: FPDF, size=10):
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
        st.warning(f"ไม่พบฟอนต์ไทย: {e} → จะพิมพ์ด้วย Helvetica (ข้อความไทยจะไม่แสดง)")
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

        pdf.set_font(pdf.font_family, size=10)
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

# ----------------------------- MAIN UI -----------------------------
st.title("🖥️ IT Asset Tracker (Google Sheets + Login + Scan + Thai PDF)")

if not do_login():
    st.stop()

gc = get_gs_client()
sh, ws_assets, ws_hist = ensure_sheets(gc)
df = read_assets_df(ws_assets)

with st.sidebar:
    st.header("เมนู")
    page = st.radio("ไปที่", [
        "แดชบอร์ด",
        "เพิ่ม/แก้ไข อุปกรณ์",
        "ค้นหา + อัปเดต",
        "พิมพ์แท็ก (PDF ไทย)",
        "ประวัติการเปลี่ยนแปลง",
        "สแกน (มือถือกล้อง) + คีย์บอร์ด",
        "นำเข้า/ส่งออก"
    ])

# ----------------------------- Pages -----------------------------
if page == "แดชบอร์ด":
    st.subheader("ภาพรวม")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("ทั้งหมด", len(df))
    c2.metric("ติดตั้งแล้ว", (df["status"]=="installed").sum() if not df.empty else 0)
    c3.metric("พร้อมใช้งาน (in_stock)", (df["status"]=="in_stock").sum() if not df.empty else 0)
    c4.metric("ซ่อม/เปลี่ยน", ((df["status"]=="repair") | (df["status"]=="replace")).sum() if not df.empty else 0)
    st.dataframe(df, use_container_width=True)

elif page == "เพิ่ม/แก้ไข อุปกรณ์":
    st.subheader("เพิ่ม/แก้ไข อุปกรณ์")
    edit_mode = st.checkbox("โหมดแก้ไข (ค้นหาด้วย Asset Tag)")
    data = {k:"" for k in ASSET_HEADERS if k not in ["id","last_update"]}
    asset_id = None

    if edit_mode:
        tag = st.text_input("ใส่ Asset Tag เพื่อโหลดข้อมูล")
        if tag and not df.empty:
            row = df[df["asset_tag"]==tag]
            if not row.empty:
                row = row.iloc[0].copy()
                asset_id = int(row.get("id")) if pd.notna(row.get("id")) else None
                for k in data.keys():
                    data[k] = row.get(k,"")

    colA, colB = st.columns(2)
    with colA:
        data["branch"] = st.text_input("รหัสสาขา (เช่น SWC001)", value=data.get("branch",""))
        auto = st.checkbox("สร้าง Asset Tag อัตโนมัติจากสาขา (ตัวเลขล้วน)", value=(not edit_mode and not data.get("asset_tag")))
        year_mode = st.selectbox("รูปแบบปี", ["yy","yyyy","(ไม่ใส่ปี)"], index=0)
        seq_len = st.number_input("จำนวนหลักของเลขรันนิ่ง", min_value=3, max_value=8, value=5)
        year_mode_val = {"yy":"yy","yyyy":"yyyy","(ไม่ใส่ปี)":""}[year_mode]
        if auto and data["branch"]:
            data["asset_tag"] = gen_next_tag_numeric(df, data["branch"], year_mode=year_mode_val, seq_len=int(seq_len))

        data["asset_tag"] = st.text_input("Asset Tag (ต้องไม่ซ้ำ / ตัวเลขเท่านั้น)", value=data.get("asset_tag",""))
        data["name"] = st.text_input("ชื่ออุปกรณ์", value=data.get("name",""))
        cat_opts = ["PC","Laptop","Printer","Switch","AP","Router","POS","Scanner","Camera","Other"]
        data["category"] = st.selectbox("หมวดหมู่", cat_opts, index=(cat_opts.index(data.get("category")) if data.get("category") in cat_opts else len(cat_opts)-1))
        data["serial_no"] = st.text_input("Serial No.", value=data.get("serial_no",""))
        data["vendor"] = st.text_input("ผู้จำหน่าย/ยี่ห้อ", value=data.get("vendor",""))
    with colB:
        st_opts = ["in_stock","installed","repair","replace","retired"]
        data["status"] = st.selectbox("สถานะ", st_opts, index=(st_opts.index(data.get("status")) if data.get("status") in st_opts else 0))
        data["location"] = st.text_input("ตำแหน่ง/จุดติดตั้ง", value=data.get("location",""))
        data["assigned_to"] = st.text_input("ผู้รับผิดชอบ/ผู้ใช้", value=data.get("assigned_to",""))
        def _dateinput(lbl, val):
            if val:
                try: return st.date_input(lbl, value=date.fromisoformat(val))
                except: return st.date_input(lbl, value=None)
            return st.date_input(lbl, value=None)
        data["purchase_date"]  = _dateinput("วันที่ซื้อ", data.get("purchase_date",""))
        data["warranty_expiry"]= _dateinput("หมดประกัน", data.get("warranty_expiry",""))
        data["installed_date"]= _dateinput("วันที่ติดตั้ง", data.get("installed_date",""))
    data["notes"] = st.text_area("บันทึกเพิ่มเติม", value=data.get("notes",""))

    for k in ["purchase_date","warranty_expiry","installed_date"]:
        if isinstance(data[k], date):
            data[k] = data[k].isoformat()

    if st.button("บันทึก/อัปเดต ✅", type="primary"):
        if not str(data["asset_tag"]).isdigit():
            st.error("Asset Tag ต้องเป็นตัวเลขล้วน")
            st.stop()
        now = datetime.now().isoformat(timespec="seconds")
        df_cur = read_assets_df(ws_assets)
        if asset_id is None:
            new_id = gen_next_id(df_cur)
            row = {"id": new_id, **data, "last_update": now}
            if (not df_cur.empty) and (row["asset_tag"] in df_cur["asset_tag"].values):
                st.error("Asset Tag ซ้ำ กรุณาเปลี่ยน")
            else:
                df_new = pd.concat([df_cur, pd.DataFrame([row])], ignore_index=True)
                write_assets_df(ws_assets, df_new)
                append_history(sh.worksheet("asset_history"), [new_id, row["asset_tag"], "CREATE", "Create asset", st.session_state.get("current_user",""), row.get("branch",""), now])
                st.success(f"บันทึกแล้ว (id={new_id}, tag={row['asset_tag']})")
        else:
            idx = df_cur.index[df_cur["id"]==asset_id]
            if len(idx)==0:
                st.error("ไม่พบ id สำหรับอัปเดต")
            else:
                i = idx[0]
                for k,v in data.items():
                    df_cur.at[i, k] = v
                df_cur.at[i, "last_update"] = now
                write_assets_df(ws_assets, df_cur)
                append_history(sh.worksheet("asset_history"), [asset_id, data.get("asset_tag",""), "UPDATE", "Update asset", st.session_state.get("current_user",""), data.get("branch",""), now])
                st.success(f"อัปเดตแล้ว (id={asset_id})")

    if edit_mode and asset_id:
        if st.button("ลบรายการนี้ 🗑️"):
            df_cur = read_assets_df(ws_assets)
            df_cur = df_cur[df_cur["id"]!=asset_id]
            write_assets_df(ws_assets, df_cur)
            append_history(sh.worksheet("asset_history"), [asset_id, data.get("asset_tag",""), "DELETE", "Delete asset", st.session_state.get("current_user",""), data.get("branch",""), datetime.now().isoformat(timespec="seconds")])
            st.warning("ลบรายการแล้ว")

elif page == "ค้นหา + อัปเดต":
    st.subheader("ค้นหา/กรอง")
    q = st.text_input("ค้นหา (asset tag / ชื่อ / serial / notes)")
    status = st.selectbox("สถานะ", ["— ทั้งหมด —","in_stock","installed","repair","replace","retired"])
    branch = st.text_input("รหัสสาขา (เว้นว่าง = ทั้งหมด)")
    cat = st.selectbox("หมวดหมู่", ["— ทั้งหมด —","PC","Laptop","Printer","Switch","AP","Router","POS","Scanner","Camera","Other"])
    dfq = df.copy()
    if q:
        _q = q.lower()
        dfq = dfq[dfq.apply(lambda r: any(_q in str(r[c]).lower() for c in ["asset_tag","name","serial_no","notes"]), axis=1)]
    if status != "— ทั้งหมด —":
        dfq = dfq[dfq["status"]==status]
    if branch:
        dfq = dfq[dfq["branch"]==branch]
    if cat != "— ทั้งหมด —":
        dfq = dfq[dfq["category"]==cat]
    st.dataframe(dfq, use_container_width=True)
    st.download_button("ดาวน์โหลดเป็น CSV", data=dfq.to_csv(index=False).encode("utf-8-sig"), file_name="assets_export.csv", mime="text/csv")

elif page == "พิมพ์แท็ก (PDF ไทย)":
    st.subheader("สร้างไฟล์ PDF สำหรับพิมพ์แท็ก (รองรับภาษาไทย)")
    st.dataframe(df, height=250)
    selected = st.multiselect("เลือก Asset Tag", options=df["asset_tag"].tolist(), default=df["asset_tag"].tolist())
    subset = df[df["asset_tag"].isin(selected)]
    colx, coly, colz = st.columns(3)
    w = colx.number_input("กว้าง (mm)", value=62)
    h = coly.number_input("สูง (mm)", value=29)
    cols = colz.number_input("จำนวนคอลัมน์ต่อแถว", min_value=1, max_value=5, value=3)
    rows_per_page = st.number_input("จำนวนแถวต่อหน้า", min_value=1, max_value=20, value=8)
    if st.button("สร้าง PDF"):
        pdf_bytes = build_labels_pdf_fpdf(subset, label_w_mm=float(w), label_h_mm=float(h), cols=int(cols), rows_per_page=int(rows_per_page))
        st.download_button("ดาวน์โหลด PDF แท็ก", data=pdf_bytes, file_name="asset_tags_thai.pdf", mime="application/pdf")

elif page == "ประวัติการเปลี่ยนแปลง":
    st.subheader("ประวัติทั้งหมด (ใหม่ล่าสุดอยู่บน)")
    try:
        ws_hist = sh.worksheet("asset_history")
        vals = ws_hist.get_all_values()
        dfh = pd.DataFrame(vals[1:], columns=vals[0]) if vals else pd.DataFrame(columns=HIST_HEADERS)
        st.dataframe(dfh.iloc[::-1].reset_index(drop=True), use_container_width=True)
    except Exception as e:
        st.error(f"โหลดประวัติไม่สำเร็จ: {e}")

elif page == "สแกน (มือถือกล้อง) + คีย์บอร์ด":
    st.subheader("โหมดสแกน")
    tab1, tab2 = st.tabs(["📷 กล้อง (HTML5)", "⌨️ คีย์บอร์ด/สแกนเนอร์"])
    with tab1:
        st.caption("สแกนด้วยกล้องมือถือ/เว็บแคม (HTML5) — ถ้าไม่ทำงานให้ใช้แท็บคีย์บอร์ดแทน")
        html = """
        <div id="reader" style="width:100%;max-width:460px"></div>
        <p id="result" style="font-family:sans-serif"></p>
        <script src="https://unpkg.com/html5-qrcode@2.3.10/minified/html5-qrcode.min.js"></script>
        <script>
          const reader = new Html5Qrcode('reader');
          function onScanSuccess(decodedText) {
            document.getElementById('result').innerText = 'Scanned: ' + decodedText;
          }
          function start() {
            const config = { fps: 10, qrbox: { width: 250, height: 250 } };
            Html5Qrcode.getCameras().then(devices => {
              if (devices && devices.length) {
                const cameraId = devices[0].id;
                reader.start(cameraId, config, onScanSuccess);
              } else {
                document.getElementById('result').innerText = 'ไม่พบกล้อง';
              }
            }).catch(err => {
              document.getElementById('result').innerText = 'เปิดกล้องไม่ได้: ' + err;
            });
          }
          start();
        </script>
        """
        components.html(html, height=520, scrolling=False)
        st.info("เมื่อสแกนแล้วให้คัดลอกค่าไปวางในแท็บคีย์บอร์ด เพื่อค้นหาอัตโนมัติ")

    with tab2:
        scanned = st.text_input("ผลลัพธ์การสแกน / พิมพ์ Asset Tag หรือ Serial")
        if st.button("ค้นหา"):
            df_match = df[(df["asset_tag"]==scanned) | (df["serial_no"]==scanned)]
            if df_match.empty:
                st.warning("ไม่พบในระบบ")
            else:
                st.dataframe(df_match)

elif page == "นำเข้า/ส่งออก":
    st.subheader("ส่งออกทั้งหมด")
    st.download_button("ดาวน์โหลด CSV ทั้งหมด", data=df.to_csv(index=False).encode("utf-8-sig"), file_name="assets_all.csv", mime="text/csv")

    st.subheader("นำเข้า/อัปเดตจาก CSV")
    st.caption("ต้องมีคอลัมน์: asset_tag,name,category,serial_no,vendor,purchase_date,warranty_expiry,status,branch,location,assigned_to,installed_date,notes")
    file = st.file_uploader("อัปโหลด CSV", type=["csv"])
    if file:
        imp = pd.read_csv(file)
        st.dataframe(imp.head())
        if st.button("นำเข้าข้อมูล"):
            now = datetime.now().isoformat(timespec="seconds")
            df_cur = read_assets_df(ws_assets)
            ok, fail = 0, 0
            for _, r in imp.iterrows():
                data = {k: ("" if pd.isna(r.get(k,"")) else str(r.get(k,""))) for k in ["asset_tag","name","category","serial_no","vendor","purchase_date","warranty_expiry","status","branch","location","assigned_to","installed_date","notes"]}
                if (not df_cur.empty) and (data["asset_tag"] in df_cur["asset_tag"].values):
                    i = df_cur.index[df_cur["asset_tag"]==data["asset_tag"]][0]
                    for k,v in data.items():
                        df_cur.at[i, k] = v
                    df_cur.at[i, "last_update"] = now
                    append_history(sh.worksheet("asset_history"), [int(df_cur.at[i,"id"]), data["asset_tag"], "IMPORT_UPDATE", "Import update", st.session_state.get("current_user",""), data.get("branch",""), now])
                else:
                    new_id = gen_next_id(df_cur)
                    row = {"id": new_id, **data, "last_update": now}
                    df_cur = pd.concat([df_cur, pd.DataFrame([row])], ignore_index=True)
                    append_history(sh.worksheet("asset_history"), [new_id, data["asset_tag"], "IMPORT_CREATE", "Import create", st.session_state.get("current_user",""), data.get("branch",""), now])
                ok += 1
            write_assets_df(ws_assets, df_cur)
            st.success(f"นำเข้าสำเร็จ {ok} รายการ, ล้มเหลว {fail}")

