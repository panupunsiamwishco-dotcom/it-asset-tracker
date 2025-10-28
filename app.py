
import streamlit as st
import pandas as pd
from datetime import datetime, date
from io import BytesIO
import qrcode

# Auth
import streamlit_authenticator as stauth

# Google Sheets
import gspread
from google.oauth2.service_account import Credentials

# QR Scanner (webcam/mobile)
try:
    from streamlit_qr_code_scanner import qr_code_scanner
    QR_COMPONENT_OK = True
except Exception:
    QR_COMPONENT_OK = False

# --- PDF via pure-Python fpdf2 ---
from fpdf import FPDF

st.set_page_config(page_title="IT Asset Tracker (GSheets + fpdf2)", page_icon="🖥️", layout="wide")

SHEET_ID = st.secrets.get("SHEET_ID", "")
GCP_INFO = dict(st.secrets.get("gcp", {}))

ASSET_HEADERS = ["id","asset_tag","name","category","serial_no","vendor","purchase_date","warranty_expiry","status","branch","location","assigned_to","installed_date","notes","last_update"]
HIST_HEADERS = ["asset_id","asset_tag","action","details","user","branch","ts"]

def get_gs_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(GCP_INFO, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc

def ensure_sheets(gc):
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

def gen_next_id(df):
    if df.empty or df["id"].isna().all():
        return 1
    return int(df["id"].max()) + 1

def gen_next_tag(df, branch_code: str) -> str:
    yy = datetime.now().strftime("%y")
    count = (df["branch"] == branch_code).sum() if "branch" in df.columns else 0
    return f"IT-{yy}{branch_code}-{count+1:04d}"

def qrcode_png_bytes(data: str, box_size=6, border=1) -> bytes:
    from io import BytesIO
    img = qrcode.make(data, box_size=box_size, border=border)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def build_labels_pdf_fpdf(rows: pd.DataFrame, label_w_mm=62, label_h_mm=29, margin_mm=5, cols=3, rows_per_page=8) -> bytes:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False, margin=0)
    page_w, page_h = (210, 297)
    col_w = label_w_mm
    row_h = label_h_mm
    x0 = margin_mm
    y0 = margin_mm

    def _new_page():
        pdf.add_page()

    _new_page()
    i = 0
    for _, r in rows.iterrows():
        col = i % cols
        row = (i // cols) % rows_per_page
        if i>0 and row==0 and col==0:
            _new_page()
        x = x0 + col*col_w
        y = y0 + row*row_h

        tag = str(r.get("asset_tag",""))
        name = str(r.get("name",""))[:28]
        branch = str(r.get("branch",""))

        pdf.set_font("Helvetica", style="B", size=10)
        pdf.text(x+2, y+6, tag)
        pdf.set_font("Helvetica", size=9)
        pdf.text(x+2, y+11, name)
        pdf.set_font("Helvetica", size=8)
        pdf.text(x+2, y+row_h-3, branch)

        qr_bytes = qrcode_png_bytes(tag, box_size=3, border=0)
        qr_x = x + col_w - 22
        qr_y = y + (row_h - 20)/2
        pdf.image_stream(qr_bytes, x=qr_x, y=qr_y, w=20, h=20)

        i += 1

    return pdf.output(dest="S").encode("latin-1")

# ----------------------------- AUTH -----------------------------
def do_login():
    # โหลด credentials จาก secrets
    raw_users = (
        st.secrets.get("auth", {})
        .get("credentials", {})
        .get("usernames", {})
    )

    # แปลงเป็นฟอร์แมตที่ streamlit-authenticator ต้องการ
    creds = {"usernames": {}}
    for uname, v in raw_users.items():
        creds["usernames"][uname] = {
            "email": v.get("email", ""),
            "name": v.get("name", ""),
            "password": v.get("password", ""),  # ต้องเป็น bcrypt hash
        }

    # ถ้าไม่มีผู้ใช้เลย ให้แจ้งและหยุด ไม่งั้น login() จะคืน None แล้วพัง
    if not creds["usernames"]:
        st.sidebar.error("ยังไม่พบผู้ใช้ใน secrets.toml → [auth.credentials.usernames.*]")
        st.stop()

    cookie_name = st.secrets.get("auth", {}).get("cookie_name", "it_asset_app")
    cookie_key = st.secrets.get("auth", {}).get("cookie_key", "change_me")

    authenticator = stauth.Authenticate(
        credentials=creds,
        cookie_name=cookie_name,
        key=cookie_key,
        cookie_expiry_days=7,
    )

    # ── สำคัญ: รองรับกรณี login() คืน None ในบางช่วง/บางเวอร์ชัน ──
    login_ret = authenticator.login(
        location="sidebar",
        fields={"Form name": "เข้าสู่ระบบ", "Username": "ผู้ใช้", "Password": "รหัสผ่าน"},
    )

    # v0.4.x ปกติควรคืน (name, authentication_status, username)
    name = auth_status = username = None
    if isinstance(login_ret, tuple) and len(login_ret) == 3:
        name, auth_status, username = login_ret
    elif isinstance(login_ret, dict):  # เผื่ออนาคตเปลี่ยนเป็น dict
        name = login_ret.get("name")
        auth_status = login_ret.get("authentication_status")
        username = login_ret.get("username")
    else:
        # ยังไม่กดปุ่ม/ยังไม่พร้อม → แสดงฟอร์มไว้แล้วหยุดตรงนี้
        st.stop()

    if auth_status:
        authenticator.logout("ออกจากระบบ", "sidebar")
        st.sidebar.success(f"ยินดีต้อนรับ {name}")
        st.session_state["current_user"] = username
        return True
    elif auth_status is False:
        st.sidebar.error("ผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
        return False
    else:
        st.stop()


# ----------------------------- MAIN -----------------------------
st.title("🖥️ IT Asset Tracker (Google Sheets + Login + Mobile Scan + fpdf2)")

if not do_login():
    st.stop()

gc = get_gs_client()
sh, ws_assets, ws_hist = ensure_sheets(gc)
df = read_assets_df(ws_assets)

with st.sidebar:
    st.header("เมนู")
    page = st.radio("ไปที่", ["แดชบอร์ด", "เพิ่ม/แก้ไข อุปกรณ์", "ค้นหา + อัปเดต", "พิมพ์แท็ก", "ประวัติการเปลี่ยนแปลง", "สแกน (มือถือกล้อง) + คีย์บอร์ด", "นำเข้า/ส่งออก"])

if page == "แดชบอร์ด":
    st.subheader("ภาพรวม")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("ทั้งหมด", len(df))
    col2.metric("ติดตั้งแล้ว", (df["status"]=="installed").sum() if not df.empty else 0)
    col3.metric("พร้อมใช้งาน (in_stock)", (df["status"]=="in_stock").sum() if not df.empty else 0)
    col4.metric("ซ่อม/เปลี่ยน", ((df["status"]=="repair") | (df["status"]=="replace")).sum() if not df.empty else 0)
    st.dataframe(df, use_container_width=True)

elif page == "เพิ่ม/แก้ไข อุปกรณ์":
    st.subheader("เพิ่ม/แก้ไข อุปกรณ์ (Google Sheets)")
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
        auto = st.checkbox("สร้าง Asset Tag อัตโนมัติจากสาขา", value=(not edit_mode and not data.get("asset_tag")))
        if auto and data["branch"]:
            data["asset_tag"] = gen_next_tag(df, data["branch"])
        data["asset_tag"] = st.text_input("Asset Tag (ต้องไม่ซ้ำ)", value=data.get("asset_tag",""))
        data["name"] = st.text_input("ชื่ออุปกรณ์", value=data.get("name",""))
        cat_opts = ["PC","Laptop","Printer","Switch","AP","Router","POS","Scanner","Camera","Other"]
        try:
            cat_idx = cat_opts.index(data.get("category","")) if data.get("category") in cat_opts else 9
        except:
            cat_idx = 9
        data["category"] = st.selectbox("หมวดหมู่", cat_opts, index=cat_idx)
        data["serial_no"] = st.text_input("Serial No.", value=data.get("serial_no",""))
        data["vendor"] = st.text_input("ผู้จำหน่าย/ยี่ห้อ", value=data.get("vendor",""))
    with colB:
        st_opts = ["in_stock","installed","repair","replace","retired"]
        try:
            st_idx = st_opts.index(data.get("status","")) if data.get("status") in st_opts else 0
        except:
            st_idx = 0
        data["status"] = st.selectbox("สถานะ", st_opts, index=st_idx)
        data["location"] = st.text_input("ตำแหน่ง/จุดติดตั้ง", value=data.get("location",""))
        data["assigned_to"] = st.text_input("ผู้รับผิดชอบ/ผู้ใช้", value=data.get("assigned_to",""))
        def _dateinput(lbl, val):
            if val:
                try:
                    return st.date_input(lbl, value=date.fromisoformat(val))
                except Exception:
                    return st.date_input(lbl, value=None)
            return st.date_input(lbl, value=None)
        data["purchase_date"] = _dateinput("วันที่ซื้อ", data.get("purchase_date",""))
        data["warranty_expiry"] = _dateinput("หมดประกัน", data.get("warranty_expiry",""))
        data["installed_date"] = _dateinput("วันที่ติดตั้ง", data.get("installed_date",""))
    data["notes"] = st.text_area("บันทึกเพิ่มเติม", value=data.get("notes",""))

    for k in ["purchase_date","warranty_expiry","installed_date"]:
        v = data[k]
        if isinstance(v, (date,)):
            data[k] = v.isoformat()

    if st.button("บันทึก/อัปเดต ✅", type="primary"):
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
    st.subheader("ค้นหา")
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

elif page == "พิมพ์แท็ก":
    st.subheader("สร้างไฟล์ PDF สำหรับพิมพ์แท็ก (fpdf2)")
    st.dataframe(df, height=250)
    selected = st.multiselect("เลือก Asset Tag", options=df["asset_tag"].tolist(), default=df["asset_tag"].tolist())
    subset = df[df["asset_tag"].isin(selected)]
    colx, coly, colz = st.columns(3)
    w = colx.number_input("กว้าง (mm)", value=62)
    h = coly.number_input("สูง (mm)", value=29)
    cols = colz.number_input("จำนวนคอลัมน์ต่อแถว", min_value=1, max_value=5, value=3)
    rows_per_page = st.number_input("จำนวนแถวต่อหน้า", min_value=1, max_value=20, value=8)
    if st.button("สร้าง PDF"):
        pdf_bytes = build_labels_pdf_fpdf(subset, label_w_mm=w, label_h_mm=h, cols=int(cols), rows_per_page=int(rows_per_page))
        st.download_button("ดาวน์โหลด PDF แท็ก", data=pdf_bytes, file_name="asset_tags.pdf", mime="application/pdf")

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
    tab1, tab2 = st.tabs(["📷 กล้อง (มือถือ/เว็บแคม)", "⌨️ คีย์บอร์ด/สแกนเนอร์"])
    with tab1:
        if QR_COMPONENT_OK:
            st.caption("ให้สิทธิ์เข้ากล้อง แล้วสแกน QR/บาร์โค้ด (รองรับมือถือ)")
            code = qr_code_scanner(key="qrscan")
            if code:
                st.success(f"สแกนได้: {code}")
                df_match = df[(df["asset_tag"]==code) | (df["serial_no"]==code)]
                if df_match.empty:
                    st.warning("ไม่พบในระบบ")
                else:
                    st.dataframe(df_match)
        else:
            st.warning("โมดูลกล้องยังไม่พร้อมในระบบนี้ ใช้แท็บคีย์บอร์ดแทน หรือแจ้งผู้ดูแลให้ติดตั้ง 'streamlit-qr-code-scanner'")
    with tab2:
        st.caption("โฟกัสที่ช่องด้านล่าง แล้วสแกนได้เลย หรือวาง (paste) ข้อความจากแอพสแกนมือถือ")
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
    st.caption("คอลัมน์ที่รองรับ: asset_tag,name,category,serial_no,vendor,purchase_date,warranty_expiry,status,branch,location,assigned_to,installed_date,notes")
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
