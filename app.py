# app.py — Single file (รวมตัวเชื่อม Google Sheets ไว้ภายใน)
from __future__ import annotations
import streamlit as st
import pandas as pd
from dataclasses import dataclass
from typing import Optional, List, Tuple

import streamlit as st

def get_sheet_id():
    # 1) ปกติอ่านจาก top-level
    sid = st.secrets.get("SHEET_ID")
    # 2) รองรับกรณีผู้ใช้ใส่ไว้ใน [gcp]
    if not sid:
        sid = (st.secrets.get("gcp", {}) or {}).get("SHEET_ID")
    # 3) รองรับสะกดเป็นตัวเล็ก
    if not sid:
        sid = st.secrets.get("sheet_id")
    # 4) ให้ใส่ชั่วคราวผ่าน UI
    if not sid:
        with st.sidebar.expander("ตั้งค่า SHEET_ID (ชั่วคราวในหน้านี้)"):
            tmp = st.text_input("SHEET_ID", value=st.session_state.get("SHEET_ID", ""))
            if st.button("ใช้ SHEET_ID นี้"):
                st.session_state["SHEET_ID"] = tmp
        sid = st.session_state.get("SHEET_ID")

    # (ออปชัน) เปิดโหมด debug ดูว่า secrets มีคีย์อะไรบ้าง
    if st.sidebar.checkbox("Debug: ดูคีย์ใน secrets"):
        st.sidebar.write({
            "top_level_keys": list(st.secrets.keys()),
            "gcp_keys": list((st.secrets.get("gcp", {}) or {}).keys()),
            "SHEET_ID_in_use": sid
        })
    return sid


# =========[ CONFIG UI ]=========
st.set_page_config(page_title="IT Asset Tracker (Google Sheets)", layout="wide")

TITLE = "💻 IT Asset Tracker (Google Sheets + Login + Thai)"
ASSETS_SHEET = "assets"
HISTORY_SHEET = "asset_history"

ASSET_COLUMNS: List[str] = [
    "id", "asset_tag", "name", "category", "serial_no", "vendor",
    "purchase_date", "warranty_expiry", "status", "branch", "location",
    "assigned_to", "installed_date", "notes", "last_update",
]
HISTORY_COLUMNS: List[str] = ["ts", "user", "action", "asset_tag", "branch", "note"]


# =========[ SIMPLE AUTH ]=========
def simple_login() -> Optional[str]:
    """Login แบบง่ายด้วย secrets.toml -> [users.<username>] password="..." """
    users = {}
    try:
        # รูปแบบใน secrets.toml:
        # [users.admin]
        # password = "1234"
        users = {u: st.secrets["users"][u]["password"] for u in st.secrets["users"]}
    except Exception:
        st.sidebar.info("ยังไม่พบผู้ใช้ใน secrets.toml → [users] รูปแบบ: [users.<username>] password=\"...\"")

    st.sidebar.header("เข้าสู่ระบบ")
    if "auth_user" not in st.session_state:
        st.session_state.auth_user = None

    if st.session_state.auth_user:
        st.sidebar.success(f"เข้าสู่ระบบสำเร็จ ({st.session_state.auth_user})")
        if st.sidebar.button("ออกจากระบบ"):
            st.session_state.auth_user = None
        return st.session_state.auth_user

    with st.sidebar.form("login_form", clear_on_submit=False):
        u = st.text_input("ผู้ใช้", value="admin")
        p = st.text_input("รหัสผ่าน", type="password")
        ok = st.form_submit_button("Login")

    if ok:
        if u in users and p == users[u]:
            st.session_state.auth_user = u
            st.sidebar.success("เข้าสู่ระบบสำเร็จ")
        else:
            st.sidebar.error("ผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
    return st.session_state.auth_user


# =========[ GSHEETS BRIDGE (รวมไว้ในไฟล์เดียว) ]=========
try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None


class GSheetsError(RuntimeError):
    """ข้อผิดพลาดฝั่ง Google Sheets แบบอ่านง่าย"""


def _require_deps():
    if gspread is None or Credentials is None:
        raise GSheetsError(
            "ไม่พบไลบรารี gspread/google-auth (เพิ่มใน requirements.txt: "
            "gspread==6.1.2, google-auth==2.34.0)"
        )


def gs_connect(secrets: dict) -> Tuple["gspread.Client", "gspread.Spreadsheet"]:
    """เชื่อมต่อ Google Sheets ด้วย service_account + SHEET_ID"""
    _require_deps()
    try:
        gcp = secrets["gcp"]
        sheet_id = secrets["SHEET_ID"]
    except KeyError as e:
        raise GSheetsError(f"ขาดค่าที่จำเป็นใน secrets.toml: {e}")

    try:
        creds = Credentials.from_service_account_info(
            gcp,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ],
        )
        client = gspread.authorize(creds)
        sh = client.open_by_key(sheet_id)
        return client, sh
    except gspread.SpreadsheetNotFound:
        raise GSheetsError("หา Spreadsheet ไม่พบ (ตรวจ SHEET_ID และสิทธิ์แชร์เป็น Editor)")
    except Exception as e:
        raise GSheetsError(f"เชื่อมต่อ Google Sheets ไม่สำเร็จ: {e}")


def _get_or_create(sh, title: str, headers: List[str]):
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers))
        ws.update([headers])
        return ws


def _ensure_header(ws, headers: List[str]):
    first = ws.row_values(1)
    if first != headers:
        ws.resize(rows=max(2, ws.row_count))
        ws.update([headers])


def gs_ensure(sh):
    """ตรวจ/สร้าง worksheets + ตั้งหัวคอลัมน์"""
    ws_assets = _get_or_create(sh, ASSETS_SHEET, ASSET_COLUMNS)
    ws_history = _get_or_create(sh, HISTORY_SHEET, HISTORY_COLUMNS)
    _ensure_header(ws_assets, ASSET_COLUMNS)
    _ensure_header(ws_history, HISTORY_COLUMNS)


@dataclass
class SheetsRepo:
    sh: "gspread.Spreadsheet"

    @property
    def ws_assets(self):
        return self.sh.worksheet(ASSETS_SHEET)

    @property
    def ws_history(self):
        return self.sh.worksheet(HISTORY_SHEET)

    def load_assets(self) -> pd.DataFrame:
        records = self.ws_assets.get_all_records()
        df = pd.DataFrame(records, columns=ASSET_COLUMNS)
        for c in ASSET_COLUMNS:
            if c not in df.columns:
                df[c] = ""
        return df

    def save_assets(self, df: pd.DataFrame) -> None:
        df = df.reindex(columns=ASSET_COLUMNS).fillna("")
        values = [ASSET_COLUMNS] + df.astype(str).values.tolist()
        self.ws_assets.clear()
        self.ws_assets.update(values)

    def append_history(self, row: dict) -> None:
        vals = [row.get(c, "") for c in HISTORY_COLUMNS]
        self.ws_history.append_row(vals)


@st.cache_resource(show_spinner=False)
def get_repo_cached() -> tuple[Optional[SheetsRepo], str]:
    """คืน (repo, state) → state in {'connected','disconnected'}"""
    try:
        _, sh = gs_connect(st.secrets)
        gs_ensure(sh)
        repo = SheetsRepo(sh)
        return repo, "connected"
    except GSheetsError as e:
        st.sidebar.error(f"Google Sheets error: {e}")
        return None, "disconnected"
    except Exception as e:
        st.sidebar.error(f"Unexpected error: {e}")
        return None, "disconnected"


# =========[ APP UI (Minimal แต่พร้อมใช้งาน) ]=========
def main():
    st.title(TITLE)

    # 1) Login
    user = simple_login()
    if not user:
        st.stop()

    # 2) Connect Sheets
    repo, state = get_repo_cached()
    if state != "connected" or repo is None:
        st.warning("ยังไม่ได้เชื่อมต่อ Google Sheets — จะทำงานแบบชั่วคราวในหน้านี้เท่านั้น", icon="⚠️")
        # โหมดชั่วคราว (local only)
        if "local_assets" not in st.session_state:
            st.session_state.local_assets = pd.DataFrame(columns=ASSET_COLUMNS)
        df = st.session_state.local_assets

        st.subheader("ตารางทรัพย์สิน (โหมดชั่วคราว)")
        df = st.data_editor(df, num_rows="dynamic", use_container_width=True)
        st.session_state.local_assets = df
        st.info("โหมดนี้จะไม่บันทึกลง Google Sheets", icon="ℹ️")
        st.stop()

    st.success("เชื่อมต่อ Google Sheets แล้ว", icon="✅")

    # 3) โหลดข้อมูล
    try:
        df_assets = repo.load_assets()
    except Exception as e:
        st.error(f"โหลดข้อมูลไม่สำเร็จ: {e}")
        df_assets = pd.DataFrame(columns=ASSET_COLUMNS)

    # 4) Summary / Counters
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("ทั้งหมด", len(df_assets))
    with c2:
        st.metric("ติดตั้งแล้ว", int((df_assets["status"] == "installed").sum()) if not df_assets.empty and "status" in df_assets else 0)
    with c3:
        st.metric("พร้อมใช้งาน", int((df_assets["status"] == "in_stock").sum()) if not df_assets.empty and "status" in df_assets else 0)
    with c4:
        st.metric("ซ่อม/เปลี่ยน", int((df_assets["status"] == "repair").sum()) if not df_assets.empty and "status" in df_assets else 0)

    st.subheader("ตารางทรัพย์สิน")
    edited_df = st.data_editor(
        df_assets,
        use_container_width=True,
        num_rows="dynamic",
        key="assets_editor",
    )

    # 5) บันทึก
    col_save, col_add = st.columns([1, 1])
    with col_save:
        if st.button("💾 บันทึกลง Google Sheets", type="primary"):
            try:
                repo.save_assets(edited_df)
                st.toast("บันทึกแล้ว", icon="✅")
            except Exception as e:
                st.error(f"บันทึกไม่สำเร็จ: {e}")

    with col_add:
        with st.expander("➕ เพิ่มรายการอย่างเร็ว"):
            new = {}
            for c in ["asset_tag", "name", "category", "serial_no", "vendor", "status", "branch"]:
                new[c] = st.text_input(c, key=f"new_{c}")
            if st.button("เพิ่มเข้า DataFrame"):
                row = {k: new.get(k, "") for k in ASSET_COLUMNS}
                edited_df = pd.concat([edited_df, pd.DataFrame([row])], ignore_index=True).fillna("")
                st.session_state["assets_editor"] = edited_df
                st.rerun()


if __name__ == "__main__":
    main()

