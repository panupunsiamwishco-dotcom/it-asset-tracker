# st_rerun_polyfill.py
# --------------------
# Safe, one-line import anywhere near the top of your app to guarantee `st.rerun` exists,
# even on older Streamlit versions (falls back to `st.experimental_rerun`).
#
# Usage in your app's main file (e.g., app.py):
#     import st_rerun_polyfill  # must be imported before any call to st.rerun()
#
# After this import you can consistently call:
#     import streamlit as st
#     st.rerun()
#
# No other changes required.

import streamlit as st

def _ensure_rerun():
    # If running on Streamlit>=1.27 (has st.rerun) do nothing.
    # If running on older versions, create an alias so existing code can call st.rerun()
    if not hasattr(st, "rerun"):
        st.rerun = st.experimental_rerun  # type: ignore[attr-defined]

_ensure_rerun()
