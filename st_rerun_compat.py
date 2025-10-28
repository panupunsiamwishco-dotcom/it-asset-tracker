
"""
st_rerun_compat.py - Drop-in compatibility for Streamlit rerun API.
"""
import streamlit as st

def rerun():
    """Call st.rerun() on new versions, fall back to experimental on old ones."""
    if hasattr(st, "rerun"):
        return st.rerun()
    return st.experimental_rerun()
