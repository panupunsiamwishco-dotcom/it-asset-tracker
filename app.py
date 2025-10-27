
import streamlit as st
import sqlite3
from contextlib import closing
from datetime import datetime, date
import pandas as pd
from io import BytesIO
import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader

DB_PATH = "assets.db"

# ----------------------------- DB LAYER -----------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_tag TEXT UNIQUE,
                name TEXT,
                category TEXT,
                serial_no TEXT,
                vendor TEXT,
                purchase_date TEXT,
                warranty_expiry TEXT,
                status TEXT,
                branch TEXT,
                location TEXT,
                assigned_to TEXT,
                installed_date TEXT,
                notes TEXT,
                last_update TEXT
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id INTEGER,
                asset_tag TEXT,
                action TEXT,
                details TEXT,
                user TEXT,
                branch TEXT,
                ts TEXT,
                FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
            );
            """
        )
        conn.commit()

def upsert_asset(data: dict, asset_id: int | None):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        now = datetime.now().isoformat(timespec="seconds")
        data = data.copy()
        data["last_update"] = now
        fields = ["asset_tag","name","category","serial_no","vendor","purchase_date",
                  "warranty_expiry","status","branch","location","assigned_to",
                  "installed_date","notes","last_update"]
        vals = [data.get(k) for k in fields]
        if asset_id is None:
            placeholders = ",".join("?" for _ in fields)
            cur.execute(f"INSERT INTO assets ({','.join(fields)}) VALUES ({placeholders})", vals)
            asset_id = cur.lastrowid
            action = "CREATE"
        else:
            set_expr = ",".join([f"{k}=?" for k in fields])
            cur.execute(f"UPDATE assets SET {set_expr} WHERE id=?", vals + [asset_id])
            action = "UPDATE"
        conn.commit()
    log_history(asset_id, data.get("asset_tag"), action, f"{action} asset", st.session_state.get("current_user","system"), data.get("branch"))
    return asset_id

def delete_asset(asset_id: int):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT asset_tag, branch FROM assets WHERE id=?", (asset_id,))
        row = cur.fetchone()
        cur.execute("DELETE FROM assets WHERE id=?", (asset_id,))
        conn.commit()
    if row:
        log_history(asset_id, row[0], "DELETE", "Delete asset", st.session_state.get("current_user","system"), row[1])

def fetch_assets(filters: dict | None = None) -> pd.DataFrame:
    query = "SELECT * FROM assets WHERE 1=1"
    params = []
    if filters:
        if filters.get("q"):
            q = f"%{filters['q']}%"
            query += " AND (asset_tag LIKE ? OR name LIKE ? OR serial_no LIKE ? OR notes LIKE ?)"
            params += [q, q, q, q]
        for k in ["status","branch","category"]:
            v = filters.get(k)
            if v and v != "— ทั้งหมด —":
                query += f" AND {k}=?"
                params.append(v)
    with closing(get_conn()) as conn:
        df = pd.read_sql_query(query, conn, params=params)
    return df

def get_asset_by_tag(asset_tag: str):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM assets WHERE asset_tag=?", (asset_tag,))
        row = cur.fetchone()
    return row

def get_asset_by_id(asset_id: int):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM assets WHERE id=?", (asset_id,))
        row = cur.fetchone()
    return row

def log_history(asset_id, asset_tag, action, details, user, branch):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO asset_history (asset_id, asset_tag, action, details, user, branch, ts) VALUES (?,?,?,?,?,?,?)",
            (asset_id, asset_tag, action, details, user, branch, datetime.now().isoformat(timespec="seconds"))
        )
        conn.commit()

def get_history(asset_id: int | None, asset_tag: str | None):
    with closing(get_conn()) as conn:
        if asset_id:
            q = "SELECT * FROM asset_history WHERE asset_id=? ORDER BY ts DESC"
            df = pd.read_sql_query(q, conn, params=[asset_id])
        else:
            q = "SELECT * FROM asset_history ORDER BY ts DESC"
            df = pd.read_sql_query(q, conn)
    return df

# ----------------------------- UTIL -----------------------------
def gen_next_tag(branch_code: str) -> str:
    # IT-<YY><BRANCH>-####
    yy = datetime.now().strftime("%y")
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM assets WHERE branch=?", (branch_code,))
        n = cur.fetchone()[0] or 0
    return f"IT-{yy}{branch_code}-{n+1:04d}"

def qrcode_png(data: str, box_size=6, border=2) -> BytesIO:
    img = qrcode.make(data, box_size=box_size, border=border)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def build_labels_pdf(rows: pd.DataFrame, label_w_mm=62, label_h_mm=29, margin_mm=5, cols=3, rows_per_page=8):
    # Simple grid on A4. Adjust as needed.
    packet = BytesIO()
    c = canvas.Canvas(packet, pagesize=A4)
    page_w, page_h = A4
    x0 = margin_mm * mm
    y0 = page_h - margin_mm * mm
    col_w = label_w_mm * mm
    row_h = label_h_mm * mm
    i = 0
    for _, r in rows.iterrows():
        col = i % cols
        row = (i // cols) % rows_per_page
        if i > 0 and row == 0 and col == 0:
            c.showPage()
            y0 = page_h - margin_mm * mm
        x = x0 + col * col_w
        y = y0 - (row + 1) * row_h
        # Draw border (optional)
        # c.rect(x, y, col_w, row_h, stroke=1, fill=0)
        # Texts
        c.setFont("Helvetica-Bold", 10)
        tag = r.get("asset_tag","")
        name = (r.get("name","") or "")[:28]
        branch = r.get("branch","")
        c.drawString(x + 2*mm, y + row_h - 6*mm, f"{tag}")
        c.setFont("Helvetica", 9)
        c.drawString(x + 2*mm, y + row_h - 11*mm, f"{name}")
        c.setFont("Helvetica", 8)
        c.drawString(x + 2*mm, y + 3*mm, f"{branch}")
        # QR
        qr_buf = qrcode_png(tag, box_size=3, border=0)
        qr_img = ImageReader(qr_buf)
        qr_size = 20 * mm
        c.drawImage(qr_img, x + col_w - qr_size - 2*mm, y + (row_h - qr_size)/2, qr_size, qr_size, mask='auto')
        i += 1
    c.save()
    packet.seek(0)
    return packet

# ----------------------------- UI -----------------------------
st.set_page_config(page_title="IT Asset Tracker", page_icon="🖥️", layout="wide")
init_db()

if "current_user" not in st.session_state:
    st.session_state.current_user = "admin"

st.title("🖥️ IT Asset Tracker (Streamlit + SQLite)")
st.caption("บันทึก/ค้นหา/แก้ไข/ลบอุปกรณ์ไอที + ประวัติ + พิมพ์แท็ก QR พร้อมโหมดสแกน (คีย์ด้วยสแกนเนอร์คีย์บอร์ด)")

with st.sidebar:
    st.header("เมนู")
    page = st.radio("ไปที่", ["แดชบอร์ด", "เพิ่ม/แก้ไข อุปกรณ์", "ค้นหา + อัปเดต", "พิมพ์แท็ก", "ประวัติการเปลี่ยนแปลง", "สแกนหาอุปกรณ์", "นำเข้า/ส่งออก"])
    st.divider()
    st.subheader("ผู้ใช้ปัจจุบัน")
    st.text_input("ชื่อผู้บันทึก (จะถูกใส่ในประวัติ)", key="current_user")

# ------------- Dashboard -------------
if page == "แดชบอร์ด":
    st.subheader("ภาพรวม")
    df = fetch_assets({})
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("ทั้งหมด", len(df))
    col2.metric("ติดตั้งแล้ว", (df.status == "installed").sum())
    col3.metric("พร้อมใช้งาน (in_stock)", (df.status == "in_stock").sum())
    col4.metric("ซ่อม/เปลี่ยน", ((df.status == "repair") | (df.status == "replace")).sum())
    st.dataframe(df)

# ------------- Create / Edit -------------
elif page == "เพิ่ม/แก้ไข อุปกรณ์":
    st.subheader("เพิ่ม/แก้ไข อุปกรณ์")
    edit_mode = st.checkbox("โหมดแก้ไข (ค้นหาด้วย Asset Tag)", value=False)
    asset_id = None
    data = {
        "asset_tag":"",
        "name":"",
        "category":"",
        "serial_no":"",
        "vendor":"",
        "purchase_date":"",
        "warranty_expiry":"",
        "status":"in_stock",
        "branch":"",
        "location":"",
        "assigned_to":"",
        "installed_date":"",
        "notes":""
    }

    if edit_mode:
        tag = st.text_input("ใส่ Asset Tag เพื่อโหลดข้อมูล")
        if tag:
            with closing(get_conn()) as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM assets WHERE asset_tag=?", (tag,))
                row = cur.fetchone()
            if row:
                cols = [c[1] for c in cur.description] if 'cur' in locals() else ["id","asset_tag","name","category","serial_no","vendor","purchase_date","warranty_expiry","status","branch","location","assigned_to","installed_date","notes","last_update"]
                rec = dict(zip(cols, row))
                asset_id = rec["id"]
                for k in data:
                    data[k] = rec.get(k,"")

    colA, colB = st.columns(2)
    with colA:
        data["branch"] = st.text_input("รหัสสาขา (เช่น SWC001)", value=data["branch"])
        auto = st.checkbox("สร้าง Asset Tag อัตโนมัติจากสาขา", value=(not edit_mode and not data["asset_tag"]))
        if auto and data["branch"]:
            data["asset_tag"] = gen_next_tag(data["branch"])
        data["asset_tag"] = st.text_input("Asset Tag (ต้องไม่ซ้ำ)", value=data["asset_tag"])
        data["name"] = st.text_input("ชื่ออุปกรณ์", value=data["name"])
        data["category"] = st.selectbox("หมวดหมู่", ["PC","Laptop","Printer","Switch","AP","Router","POS","Scanner","Camera","Other"], index=9 if data["category"]=="" else None)
        data["serial_no"] = st.text_input("Serial No.", value=data["serial_no"])
        data["vendor"] = st.text_input("ผู้จำหน่าย/ยี่ห้อ", value=data["vendor"])
    with colB:
        data["status"] = st.selectbox("สถานะ", ["in_stock","installed","repair","replace","retired"], index=0 if data["status"]=="" else None)
        data["location"] = st.text_input("ตำแหน่ง/จุดติดตั้ง", value=data["location"])
        data["assigned_to"] = st.text_input("ผู้รับผิดชอบ/ผู้ใช้", value=data["assigned_to"])
        data["purchase_date"] = st.date_input("วันที่ซื้อ", value=date.fromisoformat(data["purchase_date"]) if data["purchase_date"] else None)
        data["warranty_expiry"] = st.date_input("หมดประกัน", value=date.fromisoformat(data["warranty_expiry"]) if data["warranty_expiry"] else None)
        data["installed_date"] = st.date_input("วันที่ติดตั้ง", value=date.fromisoformat(data["installed_date"]) if data["installed_date"] else None)
    data["notes"] = st.text_area("บันทึกเพิ่มเติม", value=data["notes"])

    # normalize dates to ISO
    for k in ["purchase_date","warranty_expiry","installed_date"]:
        v = data[k]
        if isinstance(v, (date,)):
            data[k] = v.isoformat()

    if st.button("บันทึก/อัปเดต ✅", type="primary"):
        try:
            new_id = upsert_asset(data, asset_id)
            st.success(f"บันทึกแล้ว (id={new_id}, tag={data['asset_tag']})")
        except sqlite3.IntegrityError as e:
            st.error(f"ไม่สามารถบันทึกได้: {e}")

    if edit_mode and asset_id:
        if st.button("ลบรายการนี้ 🗑️"):
            delete_asset(asset_id)
            st.warning("ลบรายการแล้ว")

# ------------- Search & Update -------------
elif page == "ค้นหา + อัปเดต":
    st.subheader("ค้นหา")
    q = st.text_input("ค้นหา (asset tag / ชื่อ / serial / notes)")
    c1, c2, c3 = st.columns(3)
    status = c1.selectbox("สถานะ", ["— ทั้งหมด —","in_stock","installed","repair","replace","retired"])
    branch = c2.text_input("รหัสสาขา (เว้นว่าง = ทั้งหมด)")
    cat = c3.selectbox("หมวดหมู่", ["— ทั้งหมด —","PC","Laptop","Printer","Switch","AP","Router","POS","Scanner","Camera","Other"])
    df = fetch_assets({"q": q, "status": status, "branch": branch, "category": cat})
    st.dataframe(df, use_container_width=True)
    st.caption("คลิกที่แถวเพื่อคัดลอก asset_tag แล้วไปแก้ไขในหน้า 'เพิ่ม/แก้ไข อุปกรณ์'")
    st.download_button("ดาวน์โหลดเป็น CSV", data=df.to_csv(index=False).encode("utf-8-sig"), file_name="assets_export.csv", mime="text/csv")

# ------------- Labels -------------
elif page == "พิมพ์แท็ก":
    st.subheader("สร้างไฟล์ PDF สำหรับพิมพ์แท็ก")
    q = st.text_input("กรองรายการ (ค้นหาคล้ายหน้า 'ค้นหา')")
    df = fetch_assets({"q": q})
    st.dataframe(df, height=200)
    st.caption("เลือกแถวที่ต้องการพิมพ์แท็ก")
    selected_tags = st.multiselect("เลือก Asset Tag", options=df["asset_tag"].tolist(), default=df["asset_tag"].tolist())
    subset = df[df["asset_tag"].isin(selected_tags)]
    colx, coly, colz = st.columns(3)
    w = colx.number_input("กว้าง (mm)", value=62)
    h = coly.number_input("สูง (mm)", value=29)
    cols = colz.number_input("จำนวนคอลัมน์ต่อแถว", min_value=1, max_value=5, value=3)
    rows_per_page = st.number_input("จำนวนแถวต่อหน้า", min_value=1, max_value=20, value=8)
    if st.button("สร้าง PDF"):
        pdf = build_labels_pdf(subset, label_w_mm=w, label_h_mm=h, cols=int(cols), rows_per_page=int(rows_per_page))
        st.download_button("ดาวน์โหลด PDF แท็ก", data=pdf, file_name="asset_tags.pdf", mime="application/pdf")

# ------------- History -------------
elif page == "ประวัติการเปลี่ยนแปลง":
    st.subheader("ประวัติทั้งหมด (ใหม่ล่าสุดอยู่บน)")
    dfh = get_history(asset_id=None, asset_tag=None)
    st.dataframe(dfh, use_container_width=True)

# ------------- Scan -------------
elif page == "สแกนหาอุปกรณ์":
    st.subheader("โหมดสแกน (ใช้สแกนเนอร์บาร์โค้ด/QR ที่เชื่อมต่อเป็นคีย์บอร์ด)")
    st.caption("โฟกัสที่ช่องด้านล่าง แล้วสแกนได้เลย หรือวาง (paste) ข้อความจากแอพสแกนมือถือ")
    tag_scanned = st.text_input("ผลลัพธ์การสแกน / พิมพ์ Asset Tag", value="", key="scan_input")
    if st.button("ค้นหา"):
        row = None
        if tag_scanned:
            with closing(get_conn()) as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM assets WHERE asset_tag=? OR serial_no=?", (tag_scanned, tag_scanned))
                row = cur.fetchone()
        if row:
            cols = ["id","asset_tag","name","category","serial_no","vendor","purchase_date","warranty_expiry","status","branch","location","assigned_to","installed_date","notes","last_update"]
            rec = dict(zip(cols, row))
            st.success(f"พบ {rec['asset_tag']} - {rec['name']}")
            st.json(rec)
        else:
            st.error("ไม่พบข้อมูล")

# ------------- Import / Export -------------
elif page == "นำเข้า/ส่งออก":
    st.subheader("ส่งออกทั้งหมด")
    df = fetch_assets({})
    st.download_button("ดาวน์โหลด CSV ทั้งหมด", data=df.to_csv(index=False).encode("utf-8-sig"), file_name="assets_all.csv", mime="text/csv")

    st.subheader("นำเข้า/อัปเดตจาก CSV")
    st.caption("คอลัมน์ที่รองรับ: asset_tag,name,category,serial_no,vendor,purchase_date,warranty_expiry,status,branch,location,assigned_to,installed_date,notes")
    file = st.file_uploader("อัปโหลด CSV", type=["csv"])
    if file:
        imp = pd.read_csv(file)
        st.dataframe(imp.head())
        if st.button("นำเข้าข้อมูล"):
            ok, fail = 0, 0
            for _, r in imp.iterrows():
                data = {k: str(r.get(k,"")) if not pd.isna(r.get(k,"")) else "" for k in ["asset_tag","name","category","serial_no","vendor","purchase_date","warranty_expiry","status","branch","location","assigned_to","installed_date","notes"]}
                try:
                    # try to find by asset_tag
                    with closing(get_conn()) as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT id FROM assets WHERE asset_tag=?", (data["asset_tag"],))
                        row = cur.fetchone()
                    upsert_asset(data, row[0] if row else None)
                    ok += 1
                except Exception as e:
                    fail += 1
            st.success(f"นำเข้าสำเร็จ {ok} รายการ, ล้มเหลว {fail}")

st.markdown("---")
st.caption("เวอร์ชันสาธิตเริ่มต้น • ฐานข้อมูล SQLite ไฟล์ assets.db • พร้อมรองรับ Streamlit Cloud")
