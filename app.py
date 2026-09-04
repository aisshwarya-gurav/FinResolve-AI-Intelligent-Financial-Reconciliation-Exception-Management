
"""
AI Finance Controller — Premium FinTech Operations Console
Light mode, enterprise-grade reconciliation & exception management
"""

import io
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd
import altair as alt
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "finrca_data"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "real_data"))

from auth.models import Permission, Role
from auth.rbac import has_permission
from auth.passwords import verify_password
from auth.store import UserStore, UserNotFoundError, UserAlreadyExistsError
from auth.tokens import create_access_token
from case_management.investigation import investigate_case
from case_management.models import CaseStatus, Priority, Severity
from case_management.store import CaseStore, CaseNotFoundError
from case_management.tat import (
    compute_case_tat, compute_tat_summary, compute_exception_category_distribution,
    compute_compliance_trend, SLAStatus, SLA_TARGET_HOURS,
)
from rag.retriever import retrieve_similar
from razorpay.client import RazorpayAdapter
from razorpay.settlement import run_settlement_reconciliation, validate_settlement_csv, validate_bank_csv
from razorpay_bridge.case_ingestion import ingest_razorpay_exceptions
from razorpay_bridge.pipeline import run_razorpay_reconciliation
from case_management.ingestion import ingest_finrca_two_way, ingest_finrca_three_way
from recon_ui import (
    DataNormalizationError,
    run_two_way_upload,
    run_three_way_upload,
    ingest_two_way_results,
    ingest_three_way_results,
)

st.set_page_config(
    page_title="AI Finance Controller",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _get_user_store() -> UserStore:
    db_path = os.environ.get("CASE_DB_PATH") or os.path.abspath(
        os.path.join(os.path.dirname(__file__), "case_management", "cases.db")
    )
    return UserStore(db_path)


def _ensure_admin_user() -> None:
    email = os.environ.get("AUTH_ADMIN_EMAIL", "").strip()
    password = os.environ.get("AUTH_ADMIN_PASSWORD", "")
    if not email or not password:
        return
    db_path = os.environ.get("CASE_DB_PATH") or os.path.abspath(os.path.join(os.path.dirname(__file__), "case_management", "cases.db"))
    store = UserStore(db_path)
    try:
        store.get_by_email(email)
    except UserNotFoundError:
        try:
            store.create_user(email=email, password=password, display_name=os.environ.get("AUTH_ADMIN_DISPLAY_NAME", "").strip() or "Administrator", role=Role.ADMIN)
        except UserAlreadyExistsError:
            pass


def _get_case_store() -> CaseStore:
    db_path = os.environ.get("CASE_DB_PATH") or os.path.abspath(
        os.path.join(os.path.dirname(__file__), "case_management", "cases.db")
    )
    return CaseStore(db_path)


def _current_user() -> dict | None:
    user = st.session_state.get("auth_user")
    return user if user else None


def _has_permission(permission: Permission) -> bool:
    user = _current_user()
    if not user:
        return False
    return has_permission(user["role"], permission)


def _login_user(email: str, password: str) -> dict:
    store = _get_user_store()
    user = store.get_by_email(email)
    if not user.is_active:
        raise ValueError("Account is inactive.")
    if not verify_password(password, user.password_hash):
        raise ValueError("Invalid email or password.")

    token = create_access_token(user.user_id, user.role)
    payload = {
        "user_id": user.user_id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
    }
    st.session_state["auth_user"] = payload
    st.session_state["auth_token"] = token
    store.set_last_login(user.user_id)
    return payload


def _logout_user() -> None:
    for key in ("auth_user", "auth_token", "last_investigation", "selected_case_id"):
        st.session_state.pop(key, None)


def _safe_enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


# ============================================================================
# THEME & STYLING
# ============================================================================

def _init_theme():
    """Initialize light enterprise theme."""
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
        
        :root {
            --bg-main: #F8FAFC;
            --bg-soft: #FFFFFF;
            --text-primary: #1E293B;
            --text-secondary: #64748B;
            --text-muted: #94a3b8;
            --border-color: #E2E8F0;
            --border-light: #f1f5f9;
            --accent-primary: #2563EB;
            --accent-secondary: #3b82f6;
            --accent-light: #EFF6FF;
            --success: #16A34A;
            --success-light: #ecfdf5;
            --warning: #D97706;
            --warning-light: #fffbeb;
            --danger: #DC2626;
            --danger-light: #fef2f2;
            --info: #3b82f6;
            --info-light: #eff6ff;
        }
        
        * {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        }
        
        .stApp {
            background-color: var(--bg-main);
            color: var(--text-primary);
        }
        
        [data-testid="stSidebar"] {
            background-color: var(--bg-soft);
            border-right: 1px solid var(--border-color);
        }
        
        [data-testid="stSidebarNav"] {
            padding: 1rem 0;
        }
        
        [data-testid="stHeader"] {
            background-color: transparent;
            border-bottom: 1px solid var(--border-color);
        }
        
        .stTabs [role="tablist"] {
            border-bottom: 1px solid var(--border-color);
            gap: 1rem;
        }
        
        .stTabs [role="tab"] {
            color: var(--text-secondary);
            font-weight: 500;
            padding: 0.75rem 1rem;
            border-bottom: 2px solid transparent;
        }
        
        .stTabs [role="tab"][aria-selected="true"] {
            color: var(--accent-primary);
            border-bottom-color: var(--accent-primary);
        }
        
        .stButton > button {
            background-color: var(--bg-soft);
            color: var(--text-primary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            font-weight: 600;
            padding: 0.6rem 1.2rem;
            transition: all 0.2s;
        }
        
        .stButton > button:hover {
            border-color: var(--accent-primary);
            color: var(--accent-primary);
            background-color: var(--accent-light);
        }
        
        .stButton > button[kind="primary"] {
            background-color: var(--accent-primary);
            color: white;
            border-color: var(--accent-primary);
        }
        
        .stButton > button[kind="primary"]:hover {
            background-color: #1d4ed8;
            border-color: #1d4ed8;
        }
        
        .stTextInput > div > div > input,
        .stNumberInput > div > div > input,
        .stSelectbox > div > div > select,
        .stTextArea > div > div > textarea {
            background-color: var(--bg-soft);
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            border-radius: 6px;
            padding: 0.5rem 0.75rem;
        }
        
        .stTextInput > div > div > input:focus,
        .stNumberInput > div > div > input:focus,
        .stSelectbox > div > div > select:focus,
        .stTextArea > div > div > textarea:focus {
            border-color: var(--accent-primary);
            outline: none;
            box-shadow: 0 0 0 3px var(--accent-light);
        }
        
        .stDataFrame {
            background-color: var(--bg-soft);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            overflow: hidden;
        }
        
        .stDataFrame table {
            background-color: var(--bg-soft);
            color: var(--text-primary);
        }
        
        .stDataFrame th {
            background-color: var(--border-light);
            color: var(--text-secondary);
            font-weight: 600;
            border-color: var(--border-color);
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 0.05em;
        }
        
        .stDataFrame td {
            color: var(--text-primary);
            border-color: var(--border-light);
        }
        
        .stDataFrame tr:hover {
            background-color: var(--accent-light);
        }
        
        .stMarkdownContainer a {
            color: var(--accent-primary);
            text-decoration: none;
        }
        
        .stMarkdownContainer a:hover {
            text-decoration: underline;
        }
        
        .stFormSubmitButton > button {
            background-color: var(--accent-primary);
            color: white;
            width: 100%;
        }
        
        .stFormSubmitButton > button:hover {
            background-color: #1d4ed8;
        }
        
        /* Custom classes */
        .header-main {
            font-size: 2.5rem;
            font-weight: 800;
            color: var(--text-primary);
            letter-spacing: -0.02em;
            margin-bottom: 0.5rem;
        }
        
        .header-sub {
            font-size: 0.95rem;
            color: var(--text-secondary);
            font-weight: 500;
            margin-bottom: 1.5rem;
        }
        
        .metric-card {
            background-color: var(--bg-soft);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            min-height: 140px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            transition: all 0.2s;
        }
        
        .metric-card:hover {
            border-color: var(--accent-primary);
            box-shadow: 0 4px 12px rgba(37, 99, 235, 0.08);
        }
        
        .metric-label {
            font-size: 0.75rem;
            color: var(--text-secondary);
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.75rem;
        }
        
        .metric-value {
            font-size: 2rem;
            font-weight: 700;
            color: var(--text-primary);
            line-height: 1;
        }
        
        .metric-delta {
            font-size: 0.8rem;
            color: var(--text-secondary);
            margin-top: 0.5rem;
        }
        
        .status-badge {
            display: inline-block;
            padding: 0.35rem 0.75rem;
            border-radius: 999px;
            font-size: 0.75rem;
            font-weight: 600;
            letter-spacing: 0.05em;
            text-transform: uppercase;
        }
        
        .badge-success {
            background-color: var(--success-light);
            color: #047857;
        }
        
        .badge-warning {
            background-color: var(--warning-light);
            color: #d97706;
        }
        
        .badge-danger {
            background-color: var(--danger-light);
            color: #dc2626;
        }
        
        .badge-info {
            background-color: var(--info-light);
            color: var(--accent-primary);
        }
        
        .badge-muted {
            background-color: var(--border-light);
            color: var(--text-secondary);
        }
        
        .panel {
            background-color: var(--bg-soft);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }
        
        .empty-state {
            text-align: center;
            padding: 2rem;
            background-color: var(--border-light);
            border: 1px dashed var(--border-color);
            border-radius: 12px;
            color: var(--text-secondary);
        }
        
        .status-pill {
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.4rem 0.9rem;
            border-radius: 999px;
            background-color: var(--success-light);
            color: #047857;
            font-size: 0.8rem;
            font-weight: 600;
        }
        
        .status-dot {
            width: 0.5rem;
            height: 0.5rem;
            border-radius: 50%;
            background-color: var(--success);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


_init_theme()


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _status_badge_html(status: str, variant: str = "muted") -> str:
    """Generate HTML for status badge."""
    class_map = {
        "success": "badge-success",
        "warning": "badge-warning",
        "danger": "badge-danger",
        "info": "badge-info",
        "muted": "badge-muted",
    }
    css_class = class_map.get(variant, "badge-muted")
    return f'<span class="status-badge {css_class}">{status}</span>'


def _render_metric_card(label: str, value: str, delta: str = "") -> None:
    """Render a metric card."""
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-delta">{delta}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _format_timestamp(value: str | None) -> str:
    if not value:
        return "—"
    return str(value).replace("T", " ")[:19]


def _render_money(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) >= 1_000_000:
        return f"₹{number/1_000_000:.2f}M"
    if abs(number) >= 1_000:
        return f"₹{number/1_000:.2f}K"
    return f"₹{number:,.2f}"


def _extract_amount(record: dict[str, Any]) -> tuple[str, float | None, str]:
    amount_keys = [
        "amount", "amount_inr", "total_amount", "gross_amount", "net_amount",
        "payment_amount", "fee_amount", "fees", "principal_amount", "credit_amount",
        "debit_amount"
    ]
    currency_keys = ["currency", "txn_currency", "payment_currency", "bank_currency"]

    for key in amount_keys:
        if key in record and record[key] not in (None, "", "N/A"):
        		try:
        			amount = float(record[key])
        			currency = "INR"
        			for currency_key in currency_keys:
        				if currency_key in record and record[currency_key]:
        					currency = str(record[currency_key]).upper()
        			return _render_money(amount), amount, currency
        		except (TypeError, ValueError):
        			pass

    for key, val in record.items():
        if isinstance(val, (int, float)):
            return _render_money(val), float(val), "INR"
        if isinstance(val, str):
            try:
                parsed = float(val)
                return _render_money(parsed), parsed, "INR"
            except ValueError:
                pass

    return "—", None, "INR"


def _safe_record_payload(case) -> dict:
    try:
        data = json.loads(case.record_json)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def _allowed_statuses(current: str | CaseStatus) -> list[str]:
    current_value = current.value if isinstance(current, CaseStatus) else str(current)
    mapping = {
        "open": ["assigned", "in_review", "pending_review", "escalated"],
        "assigned": ["investigating", "in_review", "pending_review", "escalated"],
        "investigating": ["in_review", "pending_review", "escalated"],
        "in_review": ["pending_review", "approved", "resolved", "escalated"],
        "pending_review": ["approved", "resolved", "escalated"],
        "approved": ["resolved", "escalated"],
        "escalated": ["open", "assigned", "investigating", "in_review", "pending_review", "approved"],
        "resolved": [],
    }
    return mapping.get(current_value, [])


def _case_table(cases: list) -> pd.DataFrame:
    """Build case table dataframe."""
    rows = []
    for case in cases:
        record = _safe_record_payload(case)
        amount_label, _, currency = _extract_amount(record)
        rows.append({
            "Case ID": case.case_id,
            "Exception": case.exception_code,
            "Amount": amount_label,
            "Currency": currency,
            "Severity": str(case.severity).upper(),
            "Priority": str(case.priority).upper(),
            "Status": case.status,
            "Created": _format_timestamp(case.created_at),
        })
    return pd.DataFrame(rows)


def _case_table_with_sla(cases: list) -> pd.DataFrame:
    """Same as _case_table, plus Assignee and SLA Status/Time
    Remaining — used on the Cases page specifically."""
    rows = []
    for case in cases:
        record = _safe_record_payload(case)
        amount_label, _, currency = _extract_amount(record)
        tat = compute_case_tat(case)
        rows.append({
            "Case ID": case.case_id,
            "Exception Type": case.exception_code,
            "Severity": str(case.severity).upper(),
            "Priority": str(case.priority).upper(),
            "Amount": f"{amount_label} {currency}" if amount_label != "—" else "—",
            "Status": case.status,
            "Assignee": case.assignee or "Unassigned",
            "Created": _format_timestamp(case.created_at),
            "SLA Status": tat["sla_status"].replace("_", " ").upper(),
            "Time Remaining": tat["overdue_display"] and f"-{tat['overdue_display']}" or tat["time_remaining_display"] or "—",
        })
    return pd.DataFrame(rows)


def _empty_state_html(message: str) -> None:
    """Render empty state."""
    st.markdown(f'<div class="empty-state">{message}</div>', unsafe_allow_html=True)


# ============================================================================
# AUTHENTICATION
# ============================================================================

_ensure_admin_user()

if "auth_user" not in st.session_state:
    st.markdown(
        """
        <div style="
            max-width: 520px;
            margin: 5rem auto 0 auto;
            padding: 2rem;
        ">
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="header-main">Sign In</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="header-sub">AI Finance Controller</div>',
        unsafe_allow_html=True,
    )

    with st.form("login_form"):
        email = st.text_input("Email address")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button(
            "Sign in",
            type="primary",
            use_container_width=True,
        )

        if submitted:
            try:
                user = _login_user(email, password)
                st.session_state["auth_user"] = user
                st.rerun()
            except (UserNotFoundError, ValueError) as exc:
                st.error(str(exc))

    st.markdown("</div>", unsafe_allow_html=True)

    st.stop()


# ============================================================================
# SIDEBAR & NAVIGATION
# ============================================================================

with st.sidebar:
    st.markdown("## AI Finance Controller")
    st.divider()

    user = st.session_state["auth_user"]
    st.markdown(f"**{user['display_name']}**")
    st.caption(user["email"])
    st.caption(f"Role: {user['role']}")

    st.divider()

    role = user["role"]
    # Core analyst workflow first (Section 4), operational/admin pages
    # after — kept, not deleted, since Audit Log / Razorpay / Webhook
    # Monitor / System Health have real functionality this task never
    # asked to remove, just not the focus of the analyst redesign.
    all_pages = [
        ("📊", "Overview"),
        ("🔄", "Reconciliation"),
        ("📁", "Cases"),
        ("🔍", "Investigation"),
        ("⏱️", "SLA Command Center"),
        ("📈", "Reports"),
        ("📋", "Audit Log"),
        ("💳", "Razorpay"),
        ("🔌", "Webhook Monitor"),
        ("⚙️", "System Health"),
    ]

    pages_map = {
        "ADMIN": all_pages,
        "FINANCE_MANAGER": all_pages,
        "FINANCE_ANALYST": [p for p in all_pages if p[1] in [
            "Overview", "Reconciliation", "Cases", "Investigation", "SLA Command Center", "Reports", "Audit Log", "System Health"
        ]],
        "AUDITOR": [p for p in all_pages if p[1] in [
            "Overview", "Cases", "SLA Command Center", "Reports", "Audit Log", "System Health"
        ]],
        "VIEWER": [p for p in all_pages if p[1] in [
            "Overview", "Cases", "SLA Command Center", "System Health"
        ]],
    }

    available_pages = pages_map.get(role, [])
    page_names = [name for _, name in available_pages]

    page = st.radio(
        "Navigation",
        page_names,
        index=0,
        label_visibility="collapsed",
        format_func=lambda x: x,
    )

    st.divider()

    if st.button("Sign out", use_container_width=True):
        _logout_user()
        st.rerun()


# ============================================================================
# HEADER
# ============================================================================

st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
col1, col2 = st.columns([1, 1])
with col1:
    st.markdown(
        '<div class="header-main">AI Finance Controller</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="header-sub">Financial Reconciliation & Exception Operations</div>',
        unsafe_allow_html=True,
    )

# ============================================================================
# PAGE ROUTING
# ============================================================================

store = _get_case_store()


# --- OVERVIEW ---
if page == "Overview":
    st.markdown("# Finance Control Center")
    st.caption("Reconciliation performance, exceptions, investigations and SLA health")
    st.markdown(
        '<div class="status-pill"><span class="status-dot"></span>All systems operational</div>',
        unsafe_allow_html=True,
    )
    st.markdown("---")

    all_cases_raw = store.list_cases(limit=2000)
    if not all_cases_raw:
        _empty_state_html("No cases available yet. Run reconciliation to create the first exception queue.")
        st.stop()

    # ---- Global filter bar ----
    with st.expander("Filters", expanded=False):
        fc1 = st.columns(4)
        with fc1[0]:
            ov_date_from = st.date_input("Created From", value=None, key="ov_date_from")
        with fc1[1]:
            ov_date_to = st.date_input("Created To", value=None, key="ov_date_to")
        with fc1[2]:
            ov_severity = st.selectbox("Severity", ["All"] + [s.value for s in Severity], key="ov_severity")
        with fc1[3]:
            ov_status = st.selectbox("Case Status", ["All"] + [s.value for s in CaseStatus], key="ov_status")

        fc2 = st.columns(4)
        with fc2[0]:
            ov_exception = st.text_input("Exception Type contains", value="", key="ov_exception")
        with fc2[1]:
            ov_assignee = st.text_input("Assignee contains", value="", key="ov_assignee")
        with fc2[2]:
            ov_currency = st.text_input("Currency (e.g. INR)", value="", key="ov_currency")
        with fc2[3]:
            ov_method = st.text_input("Payment Method contains", value="", key="ov_method")

        if st.button("Reset Filters", key="ov_reset"):
            for k in ["ov_date_from", "ov_date_to", "ov_severity", "ov_status", "ov_exception",
                      "ov_assignee", "ov_currency", "ov_method"]:
                st.session_state.pop(k, None)
            st.rerun()

    cases = all_cases_raw
    if ov_severity != "All":
        cases = [c for c in cases if c.severity == ov_severity]
    if ov_status != "All":
        cases = [c for c in cases if c.status == ov_status]
    if ov_exception:
        cases = [c for c in cases if ov_exception.lower() in (c.exception_code or "").lower()]
    if ov_assignee:
        cases = [c for c in cases if ov_assignee.lower() in (c.assignee or "").lower()]
    if ov_date_from:
        cases = [c for c in cases if datetime.fromisoformat(c.created_at).date() >= ov_date_from]
    if ov_date_to:
        cases = [c for c in cases if datetime.fromisoformat(c.created_at).date() <= ov_date_to]

    # Amount/currency/method are inside each case's record payload —
    # only apply these filters where that data actually exists, per
    # "don't create filters that don't correspond to real data."
    def _record_field(case, *keys):
        rec = _safe_record_payload(case)
        for k in keys:
            if rec.get(k):
                return str(rec[k])
        return None

    if ov_currency:
        cases = [c for c in cases if (_record_field(c, "currency", "payment_currency", "bank_currency") or "").upper() == ov_currency.upper()]
    if ov_method:
        cases = [c for c in cases if ov_method.lower() in (_record_field(c, "mode", "method", "payment_method") or "").lower()]

    if not cases:
        _empty_state_html("No cases match the current filters.")
        st.stop()

    total_cases = len(cases)
    resolved_cases = sum(1 for c in cases if c.status == CaseStatus.RESOLVED.value)
    open_cases = total_cases - resolved_cases
    exceptions = total_cases  # every case IS an exception, by construction

    reconciled_amount = 0.0
    unmatched_amount = 0.0
    for case in cases:
        record = _safe_record_payload(case)
        _, amount, _ = _extract_amount(record)
        if amount is None:
            continue
        if case.status == CaseStatus.RESOLVED.value:
            reconciled_amount += amount
        else:
            unmatched_amount += amount

    sla_summary = compute_tat_summary(cases)
    recon_rate = (resolved_cases / total_cases * 100) if total_cases > 0 else 0

    # ---- 8 KPI cards, 2 rows of 4 ----
    row1 = st.columns(4)
    with row1[0]:
        _render_metric_card("Total Transactions", f"{total_cases:,}", "In case queue")
    with row1[1]:
        _render_metric_card("Reconciled", f"{resolved_cases:,}", _render_money(reconciled_amount))
    with row1[2]:
        _render_metric_card("Reconciliation Rate", f"{recon_rate:.1f}%", "Success rate")
    with row1[3]:
        _render_metric_card("Exceptions", f"{exceptions:,}", "Requiring investigation")

    row2 = st.columns(4)
    with row2[0]:
        _render_metric_card("Unmatched Amount", _render_money(unmatched_amount), "Unresolved value")
    with row2[1]:
        _render_metric_card("Open Cases", f"{open_cases:,}", "Currently active")
    with row2[2]:
        compliance = sla_summary["sla_compliance_percent"]
        _render_metric_card("SLA Compliance", f"{compliance}%" if compliance is not None else "—", "Resolved within target")
    with row2[3]:
        at_risk_breached = sla_summary["at_risk_count"] + sla_summary["breached_count"]
        _render_metric_card("At Risk / Breached", f"{at_risk_breached:,}", "Needs attention now")

    st.markdown("<br>", unsafe_allow_html=True)

    # ---- Reconciliation health charts ----
    chart_cols = st.columns(2)
    with chart_cols[0]:
        st.markdown("##### Exception Distribution")
        dist = {}
        for case in cases:
            dist[str(case.exception_code)] = dist.get(str(case.exception_code), 0) + 1
        if dist:
            dist_df = pd.DataFrame(sorted(dist.items(), key=lambda kv: kv[1], reverse=True)[:8], columns=["Exception", "Count"])
            dist_chart = alt.Chart(dist_df).mark_bar(color="#2563EB", cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                x=alt.X("Count:Q", title=None),
                y=alt.Y("Exception:N", sort="-x", title=None),
                tooltip=["Exception", "Count"],
            ).properties(height=260)
            st.altair_chart(dist_chart, use_container_width=True)
        else:
            _empty_state_html("No exception distribution data available.")

    with chart_cols[1]:
        st.markdown("##### Financial Exposure by Status")
        exposure_df = pd.DataFrame([
            {"Status": "Reconciled", "Amount": reconciled_amount},
            {"Status": "Unmatched", "Amount": unmatched_amount},
        ])
        exposure_chart = alt.Chart(exposure_df).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Status:N", title=None),
            y=alt.Y("Amount:Q", title=None),
            color=alt.Color("Status:N", scale=alt.Scale(domain=["Reconciled", "Unmatched"], range=["#16A34A", "#DC2626"]), legend=None),
            tooltip=["Status", "Amount"],
        ).properties(height=260)
        st.altair_chart(exposure_chart, use_container_width=True)

    st.markdown("### Recent Cases")
    table_df = _case_table(cases[:20])
    st.dataframe(table_df, use_container_width=True, hide_index=True)


# --- RECONCILIATION ---
elif page == "Reconciliation":
    if not _has_permission(Permission.RUN_RECONCILIATION):
        st.error("You do not have permission to run reconciliation.")
        st.stop()

    st.markdown("# Reconciliation")
    st.caption("Match payments against financial records and identify exceptions.")
    st.markdown("---")

    with st.expander("Reconciliation Analytics", expanded=True):
        _recon_cases = store.list_cases(limit=2000)
        if not _recon_cases:
            st.caption("No case history yet — run a reconciliation below to populate analytics.")
        else:
            a1, a2 = st.columns(2)
            with a1:
                st.markdown("##### Reconciliation Status")
                _last_run = st.session_state.get("last_reconciliation_result")
                if _last_run and _last_run.get("pipeline_result", {}).get("match_results"):
                    mr = _last_run["pipeline_result"]["match_results"]
                    status_counts = {k.replace("_", " ").title(): len(v) for k, v in mr.items() if isinstance(v, list)}
                    st.caption("From the most recent reconciliation run this session.")
                else:
                    resolved_n = sum(1 for c in _recon_cases if c.status == "resolved")
                    status_counts = {"Resolved": resolved_n, "Open Exception": len(_recon_cases) - resolved_n}
                    st.caption("Case outcomes to date (run a reconciliation for a live match breakdown).")
                status_df = pd.DataFrame(list(status_counts.items()), columns=["Status", "Count"])
                status_chart = alt.Chart(status_df).mark_arc(innerRadius=55).encode(
                    theta="Count:Q", color=alt.Color("Status:N", legend=alt.Legend(title=None)),
                    tooltip=["Status", "Count"],
                ).properties(height=240)
                st.altair_chart(status_chart, use_container_width=True)

            with a2:
                st.markdown("##### Financial Exposure")
                exposure = {"Reconciled": 0.0, "Unmatched": 0.0}
                for c in _recon_cases:
                    _, amt, _ = _extract_amount(_safe_record_payload(c))
                    if amt is None:
                        continue
                    exposure["Reconciled" if c.status == "resolved" else "Unmatched"] += amt
                exp_df = pd.DataFrame(list(exposure.items()), columns=["Status", "Amount"])
                exp_chart = alt.Chart(exp_df).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                    x=alt.X("Status:N", title=None), y=alt.Y("Amount:Q", title=None),
                    color=alt.Color("Status:N", scale=alt.Scale(domain=["Reconciled", "Unmatched"], range=["#16A34A", "#DC2626"]), legend=None),
                    tooltip=["Status", "Amount"],
                ).properties(height=240)
                st.altair_chart(exp_chart, use_container_width=True)

            st.markdown("##### Reconciliation Trend (cases created, last 14 days)")
            today = datetime.now(timezone.utc).date()
            by_day = {}
            for c in _recon_cases:
                d = datetime.fromisoformat(c.created_at).date().isoformat()
                by_day[d] = by_day.get(d, 0) + 1
            trend_rows = []
            for i in range(13, -1, -1):
                d = (today - timedelta(days=i)).isoformat()
                trend_rows.append({"date": d, "cases": by_day.get(d, 0)})
            trend_df = pd.DataFrame(trend_rows)
            trend_chart = alt.Chart(trend_df).mark_line(point=True, color="#2563EB").encode(
                x=alt.X("date:T", title=None), y=alt.Y("cases:Q", title="New Cases"),
                tooltip=["date", "cases"],
            ).properties(height=200)
            st.altair_chart(trend_chart, use_container_width=True)

    st.markdown("### Run Reconciliation")
    recon_mode = st.selectbox("Reconciliation Mode", ["2-Way", "3-Way", "Razorpay Settlement"], index=0)

    if recon_mode == "2-Way":
        st.markdown("### Input Data")
        data_source = st.radio(
            "Data Source",
            ["Upload Dataset", "Demo / Existing Data"],
            index=0,
            horizontal=True,
            key="two_way_data_source",
        )

        if data_source == "Upload Dataset":
            st.caption("Upload the two datasets required by the two-way matcher (payments and bank transactions).")
            col1, col2 = st.columns(2)
            with col1:
                payments_file = st.file_uploader("Payments / Invoice-side CSV", type=["csv"], key="two_way_payments")
            with col2:
                bank_file = st.file_uploader("Bank-side CSV", type=["csv"], key="two_way_bank")

            if st.button("Run Reconciliation", type="primary", use_container_width=True):
                try:
                    with st.spinner("Running two-way reconciliation on uploaded data..."):
                        match_results = run_two_way_upload(payments_file, bank_file)
                        created = ingest_two_way_results(
                            store=store,
                            results=match_results,
                            actor=st.session_state["auth_user"]["user_id"],
                        )
                    st.session_state["last_reconciliation_result"] = {
                        "pipeline_result": {"match_results": match_results, "mode": "2-way-upload"},
                        "created_cases": created,
                    }
                    st.success(f"Reconciliation complete: {len(created)} case(s) created.")
                except DataNormalizationError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Reconciliation failed: {type(exc).__name__}: {exc}")
        else:
            st.caption("Run the existing demo pipeline using Razorpay mock data and the bundled payments dataset.")
            col1, col2 = st.columns(2)
            with col1:
                source_type = st.selectbox("Source", ["payout", "bank_transfer"], index=0)
            with col2:
                count = st.slider("Transaction Count", min_value=1, max_value=50, value=10)

            if st.button("Run Reconciliation", type="primary", use_container_width=True):
                with st.spinner("Running reconciliation pipeline..."):
                    adapter = RazorpayAdapter(mock_mode=True)
                    pipeline_result = run_razorpay_reconciliation(
                        adapter=adapter,
                        source_type=source_type,
                        count=count,
                    )
                    created = ingest_razorpay_exceptions(
                        store=store,
                        pipeline_result=pipeline_result,
                        actor=st.session_state["auth_user"]["user_id"],
                    )
                st.session_state["last_reconciliation_result"] = {
                    "pipeline_result": pipeline_result,
                    "created_cases": created,
                }
                st.success(f"Reconciliation complete: {len(created)} case(s) created.")

        result = st.session_state.get("last_reconciliation_result")
        if result:
            st.markdown("### Reconciliation Summary")
            pr = result["pipeline_result"]
            mr = pr.get("match_results", {})

            cols = st.columns(6)
            with cols[0]:
                _render_metric_card("Processed", str(pr.get("transactions_processed", len(mr.get("matched", [])) + len(mr.get("amount_mismatch", [])) + len(mr.get("bank_no_payment", [])) + len(mr.get("payment_no_bank", [])))), "")
            with cols[1]:
                _render_metric_card("Matched", str(len(mr.get("matched", []))), "")
            with cols[2]:
                _render_metric_card("Amount Mismatch", str(len(mr.get("amount_mismatch", []))), "")
            with cols[3]:
                _render_metric_card("Bank Missing", str(len(mr.get("bank_no_payment", []))), "")
            with cols[4]:
                _render_metric_card("Payment Missing", str(len(mr.get("payment_no_bank", []))), "")
            with cols[5]:
                _render_metric_card("Cases Created", str(len(result["created_cases"])), "")
        else:
            _empty_state_html("No reconciliation run executed yet.")

    elif recon_mode == "3-Way":
        st.markdown("### Input Data")
        data_source = st.radio(
            "Data Source",
            ["Upload Dataset", "Demo / Existing Data"],
            index=0,
            horizontal=True,
            key="three_way_data_source",
        )

        if data_source == "Upload Dataset":
            st.caption("Upload the four datasets required by the three-way matcher (invoices, allocations, payments, bank).")
            col1, col2 = st.columns(2)
            with col1:
                invoices_file = st.file_uploader("Invoice / ERP CSV", type=["csv"], key="three_way_invoices")
                payments_file = st.file_uploader("Payment / Razorpay CSV", type=["csv"], key="three_way_payments")
            with col2:
                allocations_file = st.file_uploader("Payment Allocations CSV", type=["csv"], key="three_way_allocations")
                bank_file = st.file_uploader("Bank Settlement CSV", type=["csv"], key="three_way_bank")

            if st.button("Run 3-Way Reconciliation", type="primary", use_container_width=True):
                try:
                    with st.spinner("Running three-way reconciliation on uploaded data..."):
                        match_results = run_three_way_upload(
                            invoices_file,
                            allocations_file,
                            payments_file,
                            bank_file,
                        )
                        created = ingest_three_way_results(
                            store=store,
                            results=match_results,
                            actor=st.session_state["auth_user"]["user_id"],
                        )
                    st.session_state["last_reconciliation_result"] = {
                        "pipeline_result": {"match_results": match_results, "mode": "3-way-upload"},
                        "created_cases": created,
                    }
                    st.success(f"3-Way reconciliation complete: {len(created)} case(s) created.")
                except DataNormalizationError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Reconciliation failed: {type(exc).__name__}: {exc}")
        else:
            st.info("This mode uses the existing 3-way matcher pipeline and keeps the original matcher logic untouched.")
            if st.button("Run 3-Way Reconciliation", type="primary", use_container_width=True):
                with st.spinner("Running 3-way reconciliation..."):
                    before = store.count_cases()
                    ingest_finrca_three_way(store)
                    after = store.count_cases()
                st.session_state["last_reconciliation_result"] = {
                    "pipeline_result": {"mode": "3-way", "cases_before": before, "cases_after": after},
                    "created_cases": list(range(before, after)),
                }
                st.success(f"3-Way reconciliation complete. {after - before} new case(s) created.")

        result = st.session_state.get("last_reconciliation_result")
        if result:
            pr = result["pipeline_result"]
            mr = pr.get("match_results", {})
            if mr:
                st.markdown("### Reconciliation Summary")
                cols = st.columns(5)
                with cols[0]:
                    _render_metric_card("Chain Complete", str(len(mr.get("chain_complete", []))), "")
                with cols[1]:
                    _render_metric_card("Bank Exception", str(len(mr.get("chain_bank_exception", []))), "")
                with cols[2]:
                    _render_metric_card("Partial Payment", str(len(mr.get("partial_payment", []))), "")
                with cols[3]:
                    _render_metric_card("Overpaid / Duplicate", str(len(mr.get("overpaid_duplicate", []))), "")
                with cols[4]:
                    _render_metric_card("Payment w/o Invoice", str(len(mr.get("payment_without_invoice", []))), "")
            else:
                st.markdown("### Reconciliation Summary")
                st.write(f"Cases before: {pr.get('cases_before', '-')}  |  Cases after: {pr.get('cases_after', '-')}")

    else:
        st.markdown("### Razorpay Settlement Reconciliation")
        st.caption("Verify uploaded settlement records against a bank statement using UTR and net amount matching.")

        uploaded_settlement = st.file_uploader("Upload settlement CSV", type=["csv"])
        uploaded_bank = st.file_uploader("Upload bank CSV", type=["csv"])
        rule_name = st.selectbox("Matching Rule", ["utr_then_net_amount", "utr_then_amount", "transaction_id_fallback"], index=0)

        if uploaded_settlement is not None and uploaded_bank is not None:
            settlement_csv = uploaded_settlement.getvalue().decode("utf-8-sig")
            bank_csv = uploaded_bank.getvalue().decode("utf-8-sig")
            ok_settlement, settlement_errors = validate_settlement_csv(settlement_csv)
            ok_bank, bank_errors = validate_bank_csv(bank_csv)

            if ok_settlement and ok_bank:
                settlement_data = pd.read_csv(io.StringIO(settlement_csv)).to_dict(orient="records")
                bank_data = pd.read_csv(io.StringIO(bank_csv)).to_dict(orient="records")
                st.success("Settlement and bank datasets validated successfully.")

                if st.button("Run Settlement Reconciliation", type="primary", use_container_width=True):
                    with st.spinner("Matching settlement records to bank feed..."):
                        pipeline_result = run_settlement_reconciliation(
                            settlement_records=settlement_data,
                            bank_records=bank_data,
                            rule_name=rule_name,
                        )
                        created = []
                        if pipeline_result.get("unmatched_settlement"):
                            from razorpay.settlement import ingest_settlement_exceptions
                            created = ingest_settlement_exceptions(store, pipeline_result, actor=st.session_state["auth_user"]["user_id"])
                        st.session_state["last_reconciliation_result"] = {
                            "pipeline_result": pipeline_result,
                            "created_cases": created,
                        }
                    st.success(f"Settlement reconciliation complete using '{rule_name}'. {len(created)} case(s) created.")
            else:
                st.error("Dataset validation failed.")
                if settlement_errors:
                    st.write("Settlement validation:")
                    st.write(settlement_errors)
                if bank_errors:
                    st.write("Bank validation:")
                    st.write(bank_errors)

        elif uploaded_settlement is not None or uploaded_bank is not None:
            st.warning("Please upload both a settlement CSV and a bank CSV to continue.")

        if st.session_state.get("last_reconciliation_result"):
            result = st.session_state["last_reconciliation_result"]
            pr = result["pipeline_result"]
            if isinstance(pr, dict) and "matched" in pr:
                cols = st.columns(4)
                with cols[0]:
                    _render_metric_card("Matched", str(len(pr.get("matched", []))), "")
                with cols[1]:
                    _render_metric_card("Unmatched Settlement", str(len(pr.get("unmatched_settlement", []))), "")
                with cols[2]:
                    _render_metric_card("Unmatched Bank", str(len(pr.get("unmatched_bank", []))), "")
                with cols[3]:
                    _render_metric_card("Rule Used", pr.get("rule_used", "-"), "")
                if pr.get("matched"):
                    st.json(pr["matched"][:5])


# --- EXCEPTIONS ---
elif page == "Cases":
    st.markdown("# Cases")
    st.caption("Exception cases surfaced from reconciliation — review, assign, resolve, and investigate.")
    st.markdown("---")

    cases = store.list_cases(limit=1000)
    if not cases:
        _empty_state_html("No cases available. Run reconciliation to ingest exceptions.")
        st.stop()

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        status_filter = st.selectbox("Status", ["All"] + [s.value for s in CaseStatus])
    with col2:
        severity_filter = st.selectbox("Severity", ["All"] + [s.value for s in Severity])
    with col3:
        priority_filter = st.selectbox("Priority", ["All"] + [p.value for p in Priority])
    with col4:
        sla_filter = st.selectbox("SLA Status", ["All"] + [s.value.replace("_", " ").title() for s in SLAStatus])
    with col5:
        search = st.text_input("Search Case ID / Assignee")

    filtered = []
    for case in cases:
        if status_filter != "All" and case.status != status_filter:
            continue
        if severity_filter != "All" and str(case.severity).lower() != severity_filter.lower():
            continue
        if priority_filter != "All" and str(case.priority).lower() != priority_filter.lower():
            continue
        if search and search.lower() not in case.case_id.lower() and search.lower() not in (case.assignee or "").lower():
            continue
        if sla_filter != "All" and compute_case_tat(case)["sla_status"] != sla_filter.lower().replace(" ", "_"):
            continue
        filtered.append(case)

    if not filtered:
        _empty_state_html("No cases match the active filters.")
        st.stop()

    st.markdown("### Case Queue")
    table_df = _case_table_with_sla(filtered)
    st.dataframe(table_df, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("### Case Details")

    selected_case_id = st.selectbox(
        "Select case to review",
        [c.case_id for c in filtered],
        index=0,
        label_visibility="collapsed",
    )
    selected = store.get_case(selected_case_id)
    record = _safe_record_payload(selected)
    amount_label, _, currency = _extract_amount(record)

    # Case header
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"#### {selected.case_id}")
    with col2:
        variant = "success" if selected.status == "resolved" else "warning"
        st.markdown(
            _status_badge_html(selected.status.upper(), variant),
            unsafe_allow_html=True,
        )

    # Case metadata
    meta_cols = st.columns(6)
    meta_cols[0].markdown(f"**Status**  \n{selected.status}")
    meta_cols[1].markdown(f"**Severity**  \n{str(selected.severity).upper()}")
    meta_cols[2].markdown(f"**Priority**  \n{str(selected.priority).upper()}")
    meta_cols[3].markdown(f"**Source**  \n{selected.source_pipeline}")
    meta_cols[4].markdown(f"**Created**  \n{_format_timestamp(selected.created_at)}")
    meta_cols[5].markdown(f"**Updated**  \n{_format_timestamp(selected.updated_at)}")

    # Case details and investigation
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Transaction Details")
        st.markdown(f"**Exception Code**: {selected.exception_code}")
        st.markdown(f"**Amount**: {amount_label} ({currency})")
        st.markdown(f"**Source Pipeline**: {selected.source_pipeline}")
        if record:
            clean = {k: v for k, v in record.items() if not isinstance(v, (dict, list))}
            if clean:
                st.json(clean)

    with col2:
        st.markdown("#### Investigation")
        if _has_permission(Permission.INVESTIGATE_CASE):
            if st.button("Run Investigation", type="primary", use_container_width=True):
                with st.spinner("Running investigation..."):
                    try:
                        result = investigate_case(store, selected.case_id, k=3)
                        st.session_state["last_investigation"] = result
                    except Exception as exc:
                        st.session_state["last_investigation"] = {
                            "verified": False,
                            "confidence": 0.0,
                            "confirmed_facts": f"Investigation failed: {exc}",
                            "historical_precedent": "Unavailable",
                            "likely_hypothesis": "Manual review required",
                            "suggested_action": "Escalate for human review",
                            "retrieved_case_ids": [],
                        }

        inv_result = st.session_state.get("last_investigation")
        if inv_result:
            verified = bool(inv_result.get("verified"))
            variant = "success" if verified else "warning"
            st.markdown(
                _status_badge_html("Verified" if verified else "Unverified", variant),
                unsafe_allow_html=True,
            )
            st.markdown(f"**Confidence**: {float(inv_result.get('confidence', 0.0) or 0.0):.0%}")
            st.markdown(f"**Root Cause**: {inv_result.get('confirmed_facts', 'Unavailable')}")
            st.markdown(f"**Recommended Action**: {inv_result.get('suggested_action', 'Unavailable')}")

    st.markdown("---")
    st.markdown("### Case Actions")

    action_col1, action_col2, action_col3 = st.columns(3)

    with action_col1:
        if _has_permission(Permission.ADD_NOTE):
            note = st.text_area("Add note", key=f"note_{selected.case_id}", height=100)
            if st.button("Save note", use_container_width=True):
                if note.strip():
                    store.add_note(selected.case_id, st.session_state["auth_user"]["user_id"], note.strip())
                    st.success("Note added.")
                    st.rerun()

    with action_col2:
        if _has_permission(Permission.CHANGE_STATUS):
            choices = _allowed_statuses(selected.status)
            if choices:
                next_status = st.selectbox("Change status", choices, key=f"status_{selected.case_id}")
                if st.button("Update status", use_container_width=True):
                    try:
                        store.update_status(selected.case_id, CaseStatus(next_status), st.session_state["auth_user"]["user_id"])
                        st.success(f"Status updated to {next_status}.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

    with action_col3:
        if _has_permission(Permission.RESOLVE_CASE):
            resolve_note = st.text_area("Resolution note", key=f"resolve_{selected.case_id}", height=100)
            if st.button("Resolve case", use_container_width=True):
                if resolve_note.strip():
                    try:
                        store.resolve_case(selected.case_id, st.session_state["auth_user"]["user_id"], resolve_note.strip())
                        st.success("Case resolved.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

    # Notes
    st.markdown("### Case Notes")
    notes = store.get_notes(selected.case_id)
    if not notes:
        st.caption("No notes yet.")
    else:
        for note in notes:
            st.markdown(f"**{note.author}** · {note.created_at}  \n{note.text}")
            st.markdown("---")


# --- INVESTIGATION ---
elif page == "Investigation":
    st.markdown("# Investigation")
    st.caption("AI-powered root-cause analysis grounded in historical case evidence.")
    st.markdown("---")

    cases = store.list_cases(limit=500)
    if not cases:
        _empty_state_html("No cases available.")
        st.stop()

    selected_case_id = st.selectbox("Select case", [c.case_id for c in cases], index=0)
    case = store.get_case(selected_case_id)
    record = _safe_record_payload(case)
    amount_label, amount_value, currency = _extract_amount(record)

    # ---- Case Header ----
    variant_map = {"resolved": "success", "escalated": "danger"}
    status_variant = variant_map.get(case.status, "warning")
    st.markdown(
        f"""
        <div class="metric-card" style="margin-bottom: 1rem;">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:1rem;">
                <div>
                    <div style="font-size:1.3rem; font-weight:700; color:var(--text-primary);">Case #{case.case_id}</div>
                    <div style="color:var(--text-secondary); margin-top:4px;">{case.exception_code.replace('_', ' ').title()}</div>
                </div>
                <div style="text-align:right;">
                    <div class="metric-label">Payment Amount</div>
                    <div style="font-size:1.2rem; font-weight:700; color:var(--text-primary);">{amount_label} {currency if amount_value is not None else ''}</div>
                </div>
            </div>
            <div style="display:flex; gap:0.75rem; margin-top:12px; flex-wrap:wrap;">
                {_status_badge_html('SEVERITY: ' + str(case.severity).upper(), 'danger' if case.severity == 'critical' else ('warning' if case.severity == 'high' else 'info'))}
                {_status_badge_html('STATUS: ' + case.status.upper(), status_variant)}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ---- Case-level SLA strip ----
    _tat = compute_case_tat(case)
    _status_variant = {"met": "success", "at_risk": "warning", "breached": "danger", "pending": "info"}
    st.markdown(
        f"""
        <div class="metric-card" style="margin-bottom: 1rem;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:1.5rem;">
                <div>
                    <div class="metric-label">SLA STATUS</div>
                    <div style="margin-top:4px;">{_status_badge_html(_tat['sla_status'].upper().replace('_',' '), _status_variant.get(_tat['sla_status'], 'muted'))}</div>
                </div>
                <div>
                    <div class="metric-label">SLA DEADLINE</div>
                    <div class="metric-value" style="font-size:1.1rem;">{_format_timestamp(_tat['sla_deadline_iso'])}</div>
                </div>
                <div>
                    <div class="metric-label">{'OVERDUE' if _tat['overdue_display'] else ('RESOLVED IN' if _tat['is_resolved'] else 'TIME REMAINING')}</div>
                    <div class="metric-value" style="font-size:1.1rem;">{_tat['overdue_display'] or _tat['resolution_display'] or _tat['time_remaining_display'] or '—'}</div>
                </div>
                <div>
                    <div class="metric-label">TARGET</div>
                    <div class="metric-value" style="font-size:1.1rem;">{_tat['sla_target_hours']}h</div>
                </div>
                <div>
                    <div class="metric-label">CASE CREATED</div>
                    <div class="metric-value" style="font-size:1.1rem;">{_format_timestamp(case.created_at)}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ---- Two-column: Evidence (left) / AI Investigation (right) ----
    evid_col, ai_col = st.columns(2)

    with evid_col:
        st.markdown("#### Transaction Evidence")

        # Surface the fields a reconciliation record commonly carries,
        # if present — never invents values that aren't in the record.
        erp_amount = record.get("payment_amount") or record.get("erp_amount") or record.get("invoice_amount")
        bank_amount = record.get("bank_amount") or record.get("amount")
        diff = None
        if erp_amount is not None and bank_amount is not None:
            try:
                diff = float(erp_amount) - float(bank_amount)
            except (TypeError, ValueError):
                diff = None

        ev_rows = []
        for label, key_candidates in [
            ("Invoice / ERP Amount", ["invoice_amount", "erp_amount", "payment_amount"]),
            ("Payment Gateway Amount", ["gateway_amount", "razorpay_amount", "payment_amount"]),
            ("Bank Settlement Amount", ["bank_amount", "settlement_amount", "amount"]),
            ("Currency", ["currency", "payment_currency", "bank_currency"]),
            ("Payment ID", ["payment_id", "razorpay_payment_id"]),
            ("Bank Reference", ["bank_reference", "utr", "reference_number"]),
            ("Transaction Date", ["transaction_date", "payment_date", "settlement_date"]),
        ]:
            for k in key_candidates:
                if record.get(k) not in (None, ""):
                    ev_rows.append({"Field": label, "Value": str(record[k])})
                    break

        if ev_rows:
            st.dataframe(pd.DataFrame(ev_rows), use_container_width=True, hide_index=True)
        else:
            st.caption("No structured transaction fields found in this record.")

        if diff is not None and abs(diff) > 0.005:
            st.markdown(
                f"""
                <div class="metric-card" style="border-left: 3px solid var(--danger); margin-top:0.75rem;">
                    <div class="metric-label">Amount Discrepancy</div>
                    <div style="margin-top:6px;">ERP: <b>{_render_money(erp_amount)}</b> &nbsp;|&nbsp; Bank: <b>{_render_money(bank_amount)}</b></div>
                    <div style="margin-top:4px; color:var(--danger); font-weight:600;">Difference: {_render_money(diff)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with st.expander("Full raw record"):
            clean = {k: v for k, v in record.items() if not isinstance(v, (dict, list))}
            if clean:
                st.json(clean)

    with ai_col:
        st.markdown("#### AI Root Cause Analysis")
        if _has_permission(Permission.INVESTIGATE_CASE):
            if st.button("Run Investigation", type="primary", use_container_width=True):
                with st.spinner("Running investigation..."):
                    try:
                        result = investigate_case(store, case.case_id, k=3)
                        st.session_state["last_investigation"] = result
                    except Exception as exc:
                        st.session_state["last_investigation"] = {
                            "verified": False,
                            "confidence": 0.0,
                            "confirmed_facts": f"Investigation failed: {exc}",
                            "historical_precedent": "Unavailable",
                            "likely_hypothesis": "Manual review required",
                            "suggested_action": "Escalate for human review",
                            "retrieved_case_ids": [],
                        }
        else:
            st.caption("Your role does not have investigation permission.")

        result = st.session_state.get("last_investigation")
        if not result:
            _empty_state_html("Run investigation to see AI analysis.")
        else:
            verified = bool(result.get("verified"))
            variant = "success" if verified else "warning"
            st.markdown(_status_badge_html("Verified" if verified else "Unverified", variant), unsafe_allow_html=True)

            st.markdown("**Likely Cause**")
            st.write(result.get("confirmed_facts", "Unavailable"))

            st.markdown("**Evidence**")
            st.write(result.get("historical_precedent", "Unavailable"))

            st.markdown(f"**Confidence**: {float(result.get('confidence', 0.0) or 0.0):.0%}")

            st.markdown("**Recommended Action**")
            st.write(result.get("suggested_action", "Unavailable"))

            if result.get("retrieved_case_ids"):
                st.markdown("**Similar Historical Cases**")
                st.write(", ".join(result["retrieved_case_ids"]))

    st.markdown("---")

    # ---- Timeline (real audit events only, never fabricated) ----
    st.markdown("#### Investigation Timeline")
    try:
        events = store.get_audit_history(case.case_id)
    except Exception:
        events = []

    if not events:
        st.caption("No audit events recorded yet for this case.")
    else:
        stage_labels = {
            "created": "Case Created",
            "assigned": "Assigned",
            "status_changed": "Status Changed",
            "rag_investigated": "AI Analysis",
            "note_added": "Note Added",
            "resolved": "Resolved",
            "authorization_denied": "Action Denied",
        }
        timeline_html = ['<div style="display:flex; flex-direction:column; gap:0;">']
        for i, ev in enumerate(events):
            label = stage_labels.get(ev.event_type, ev.event_type.replace("_", " ").title())
            timeline_html.append(
                f"""
                <div style="display:flex; gap:12px; padding:10px 0; border-left:2px solid var(--border-color); margin-left:8px; padding-left:20px; position:relative;">
                    <div style="position:absolute; left:-7px; top:14px; width:12px; height:12px; border-radius:50%; background:var(--accent-primary);"></div>
                    <div>
                        <div style="font-weight:600; color:var(--text-primary);">{label}</div>
                        <div style="color:var(--text-secondary); font-size:0.85rem;">{_format_timestamp(ev.created_at)} — {ev.actor or 'system'}</div>
                        <div style="color:var(--text-muted); font-size:0.85rem;">{ev.detail}</div>
                    </div>
                </div>
                """
            )
        timeline_html.append("</div>")
        st.markdown("".join(timeline_html), unsafe_allow_html=True)


# --- SLA DASHBOARD ---
elif page == "SLA Command Center":
    st.markdown("Enterprise SLA monitoring across all reconciliation exceptions.")
    st.markdown("---")

    can_view_aggregate = _has_permission(Permission.EXPORT_DATA)

    # ---- Filters ----
    with st.expander("Filters", expanded=False):
        f_cols = st.columns(4)
        with f_cols[0]:
            f_sla_status = st.selectbox("SLA Status", ["All"] + [s.value.replace("_", " ").title() for s in SLAStatus], key="sla_f_status")
        with f_cols[1]:
            f_severity = st.selectbox("Severity", ["All"] + [s.value for s in Severity], key="sla_f_severity")
        with f_cols[2]:
            f_priority = st.selectbox("Priority", ["All"] + [p.value for p in Priority], key="sla_f_priority")
        with f_cols[3]:
            f_case_status = st.selectbox("Case Status", ["All"] + [s.value for s in CaseStatus], key="sla_f_case_status")

        f_cols2 = st.columns(4)
        with f_cols2[0]:
            f_exception_code = st.text_input("Exception Type contains", value="", key="sla_f_exc")
        with f_cols2[1]:
            f_assignee = st.text_input("Assigned User contains", value="", key="sla_f_assignee")
        with f_cols2[2]:
            f_date_from = st.date_input("Created From", value=None, key="sla_f_date_from")
        with f_cols2[3]:
            f_date_to = st.date_input("Created To", value=None, key="sla_f_date_to")

        if st.button("Reset Filters"):
            for k in ["sla_f_status", "sla_f_severity", "sla_f_priority", "sla_f_case_status",
                      "sla_f_exc", "sla_f_assignee", "sla_f_date_from", "sla_f_date_to"]:
                st.session_state.pop(k, None)
            st.rerun()

    # ---- Fetch + apply filters ----
    all_cases = store.list_cases(
        severity=None if f_severity == "All" else f_severity,
        priority=None if f_priority == "All" else f_priority,
        status=None if f_case_status == "All" else f_case_status,
        limit=5000,
    )
    if f_exception_code:
        all_cases = [c for c in all_cases if f_exception_code.lower() in (c.exception_code or "").lower()]
    if f_assignee:
        all_cases = [c for c in all_cases if f_assignee.lower() in (c.assignee or "").lower()]
    if f_date_from:
        all_cases = [c for c in all_cases if datetime.fromisoformat(c.created_at).date() >= f_date_from]
    if f_date_to:
        all_cases = [c for c in all_cases if datetime.fromisoformat(c.created_at).date() <= f_date_to]

    per_case_tat = {c.case_id: compute_case_tat(c) for c in all_cases}
    if f_sla_status != "All":
        wanted = f_sla_status.lower().replace(" ", "_")
        all_cases = [c for c in all_cases if per_case_tat[c.case_id]["sla_status"] == wanted]

    filtered_cases = all_cases
    summary = compute_tat_summary(filtered_cases)

    if not filtered_cases:
        _empty_state_html("No cases match the current filters.")
    elif not can_view_aggregate:
        st.info("Your role has read-only case-level access. Aggregate KPIs and charts require export permission.")
    else:
        # ---- KPI cards (2 rows of 4) ----
        row1 = st.columns(4)
        with row1[0]:
            _render_metric_card("Total Cases", str(summary["total_cases"]), "")
        with row1[1]:
            _render_metric_card("Open Cases", str(summary["open_count"]), "")
        with row1[2]:
            _render_metric_card("SLA Met", str(summary["met_count"]), "")
        with row1[3]:
            _render_metric_card("SLA At Risk", str(summary["at_risk_count"]), "")

        row2 = st.columns(4)
        with row2[0]:
            _render_metric_card("SLA Breached", str(summary["breached_count"]), "")
        with row2[1]:
            compliance = summary["sla_compliance_percent"]
            _render_metric_card("SLA Compliance", f"{compliance}%" if compliance is not None else "—", "Resolved cases only")
        with row2[2]:
            avg = summary["avg_resolution_hours"]
            _render_metric_card("Avg Resolution Time", f"{avg}h" if avg is not None else "—", "")
        with row2[3]:
            _render_metric_card("Critical Cases", str(summary["critical_open_count"]), "Open & unresolved")

        st.markdown("<br>", unsafe_allow_html=True)

        # ---- Charts ----
        chart_row1 = st.columns(2)
        with chart_row1[0]:
            st.markdown("##### SLA Status Distribution")
            status_df = pd.DataFrame({
                "Status": ["Pending", "At Risk", "Met", "Breached"],
                "Count": [summary["pending_count"], summary["at_risk_count"], summary["met_count"], summary["breached_count"]],
            })
            status_colors = {"Pending": "#3b82f6", "At Risk": "#f59e0b", "Met": "#10b981", "Breached": "#ef4444"}
            donut = alt.Chart(status_df).mark_arc(innerRadius=60).encode(
                theta="Count:Q",
                color=alt.Color("Status:N", scale=alt.Scale(domain=list(status_colors.keys()), range=list(status_colors.values())), legend=alt.Legend(title=None)),
                tooltip=["Status", "Count"],
            ).properties(height=280)
            st.altair_chart(donut, use_container_width=True)

        with chart_row1[1]:
            st.markdown("##### Cases by Severity")
            sev_df = pd.DataFrame([
                {"Severity": sev.value.upper(), "Count": summary["by_severity"][sev.value]["total"]}
                for sev in Severity
            ])
            sev_colors = {"CRITICAL": "#ef4444", "HIGH": "#f59e0b", "MEDIUM": "#3b82f6", "LOW": "#9ca3af"}
            bar = alt.Chart(sev_df).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                x=alt.X("Severity:N", sort=list(sev_colors.keys()), title=None),
                y=alt.Y("Count:Q", title=None),
                color=alt.Color("Severity:N", scale=alt.Scale(domain=list(sev_colors.keys()), range=list(sev_colors.values())), legend=None),
                tooltip=["Severity", "Count"],
            ).properties(height=280)
            st.altair_chart(bar, use_container_width=True)

        chart_row2 = st.columns(2)
        with chart_row2[0]:
            st.markdown("##### Resolution Time vs SLA Target")
            resol_df = pd.DataFrame([
                {"Severity": sev.value.upper(), "Type": "Target", "Hours": SLA_TARGET_HOURS[sev]}
                for sev in Severity
            ] + [
                {"Severity": sev.value.upper(), "Type": "Actual Avg", "Hours": summary["by_severity"][sev.value]["avg_resolution_hours"] or 0}
                for sev in Severity
            ])
            grouped_bar = alt.Chart(resol_df).mark_bar().encode(
                x=alt.X("Severity:N", sort=["CRITICAL", "HIGH", "MEDIUM", "LOW"], title=None),
                y=alt.Y("Hours:Q", title="Hours"),
                color=alt.Color("Type:N", scale=alt.Scale(domain=["Target", "Actual Avg"], range=["#d1d5db", "#2563eb"]), legend=alt.Legend(title=None)),
                xOffset="Type:N",
                tooltip=["Severity", "Type", "Hours"],
            ).properties(height=280)
            st.altair_chart(grouped_bar, use_container_width=True)

        with chart_row2[1]:
            st.markdown("##### Exception Category Distribution")
            cat_dist = compute_exception_category_distribution(filtered_cases)
            cat_df = pd.DataFrame(list(cat_dist.items()), columns=["Category", "Count"]).head(8)
            if cat_df.empty:
                st.caption("No exception data available.")
            else:
                cat_bar = alt.Chart(cat_df).mark_bar(color="#2563eb", cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                    x=alt.X("Count:Q", title=None),
                    y=alt.Y("Category:N", sort="-x", title=None),
                    tooltip=["Category", "Count"],
                ).properties(height=280)
                st.altair_chart(cat_bar, use_container_width=True)

        st.markdown("##### SLA Compliance Trend (30 days)")
        trend = compute_compliance_trend(filtered_cases, days=30)
        trend_df = pd.DataFrame(trend)
        trend_df_plot = trend_df.dropna(subset=["compliance_percent"])
        if trend_df_plot.empty:
            st.caption("Not enough resolved cases yet to show a compliance trend.")
        else:
            line = alt.Chart(trend_df_plot).mark_line(point=True, color="#2563eb").encode(
                x=alt.X("date:T", title=None),
                y=alt.Y("compliance_percent:Q", title="Compliance %", scale=alt.Scale(domain=[0, 100])),
                tooltip=["date", "compliance_percent", "resolved_count"],
            ).properties(height=250)
            st.altair_chart(line, use_container_width=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # ---- Attention Required ----
        st.markdown("### Attention Required")
        attention_cases = [
            c for c in filtered_cases
            if per_case_tat.get(c.case_id, compute_case_tat(c))["sla_status"] in ("breached", "at_risk")
            or (c.severity == "critical" and c.status != "resolved")
        ]
        if not attention_cases:
            st.success("No cases currently need attention.")
        else:
            rows = []
            for c in attention_cases:
                tat = per_case_tat.get(c.case_id, compute_case_tat(c))
                amount_label, _, currency = _extract_amount(_safe_record_payload(c))
                # Priority ranking per the requested order: Breached,
                # At Risk, Critical, High — lower number sorts first.
                if tat["sla_status"] == "breached":
                    rank = 0
                elif tat["sla_status"] == "at_risk":
                    rank = 1
                elif c.severity == "critical":
                    rank = 2
                elif c.severity == "high":
                    rank = 3
                else:
                    rank = 4
                rows.append({
                    "_rank": rank,
                    "Case ID": c.case_id,
                    "Exception": c.exception_code,
                    "Priority": c.priority.upper(),
                    "Severity": c.severity.upper(),
                    "Amount": f"{amount_label} {currency}" if amount_label != "—" else "—",
                    "Created": _format_timestamp(c.created_at),
                    "SLA Deadline": _format_timestamp(tat["sla_deadline_iso"]),
                    "Time Remaining / Overdue": tat["overdue_display"] or tat["time_remaining_display"] or "—",
                    "Status": c.status,
                    "Assigned": c.assignee or "Unassigned",
                })
            attention_df = pd.DataFrame(rows).sort_values(by="_rank", ascending=True).drop(columns=["_rank"])
            st.dataframe(attention_df, use_container_width=True, hide_index=True)


# --- REPORTS ---
elif page == "Reports":
    st.markdown("# Reports")
    st.caption("Financial and reconciliation analytics for the current case portfolio.")
    st.markdown("---")

    if not _has_permission(Permission.EXPORT_DATA):
        st.info("Your role has read-only access. Reports require export permission — contact an Admin or Finance Manager if you need this.")
        st.stop()

    report_cases = store.list_cases(limit=5000)
    if not report_cases:
        _empty_state_html("No case data available to report on yet.")
        st.stop()

    total = len(report_cases)
    resolved = sum(1 for c in report_cases if c.status == "resolved")
    sla_summary = compute_tat_summary(report_cases)

    reconciled_amt, unmatched_amt = 0.0, 0.0
    for c in report_cases:
        _, amt, _ = _extract_amount(_safe_record_payload(c))
        if amt is None:
            continue
        if c.status == "resolved":
            reconciled_amt += amt
        else:
            unmatched_amt += amt

    st.markdown("### Reconciliation Summary")
    r1 = st.columns(3)
    with r1[0]:
        _render_metric_card("Total Cases", f"{total:,}", "")
    with r1[1]:
        _render_metric_card("Resolved", f"{resolved:,}", f"{resolved/total*100:.1f}%" if total else "")
    with r1[2]:
        _render_metric_card("Open", f"{total-resolved:,}", "")

    st.markdown("### Exception Summary")
    exc_dist = compute_exception_category_distribution(report_cases)
    exc_df = pd.DataFrame(list(exc_dist.items()), columns=["Exception Type", "Count"])
    st.dataframe(exc_df, use_container_width=True, hide_index=True)

    st.markdown("### Financial Exposure")
    f1 = st.columns(3)
    with f1[0]:
        _render_metric_card("Reconciled Value", _render_money(reconciled_amt), "")
    with f1[1]:
        _render_metric_card("Unmatched Value", _render_money(unmatched_amt), "")
    with f1[2]:
        total_val = reconciled_amt + unmatched_amt
        _render_metric_card("Total Exposure", _render_money(total_val), "")

    st.markdown("### SLA Performance")
    s1 = st.columns(4)
    with s1[0]:
        compliance = sla_summary["sla_compliance_percent"]
        _render_metric_card("SLA Compliance", f"{compliance}%" if compliance is not None else "—", "")
    with s1[1]:
        _render_metric_card("Met", str(sla_summary["met_count"]), "")
    with s1[2]:
        _render_metric_card("At Risk", str(sla_summary["at_risk_count"]), "")
    with s1[3]:
        _render_metric_card("Breached", str(sla_summary["breached_count"]), "")

    st.markdown("### Case Resolution")
    avg_res = sla_summary["avg_resolution_hours"]
    med_res = sla_summary["median_resolution_hours"]
    c1 = st.columns(2)
    with c1[0]:
        _render_metric_card("Avg Resolution Time", f"{avg_res}h" if avg_res is not None else "—", "")
    with c1[1]:
        _render_metric_card("Median Resolution Time", f"{med_res}h" if med_res is not None else "—", "")

    st.markdown("---")
    st.caption(
        "Export functionality is provided by the existing Case Management API "
        "(GET /reports/tat-summary) for programmatic access — this page is a "
        "read-only in-app summary of the same underlying data."
    )


elif page == "Audit Log":
    st.markdown("Complete audit trail of all case actions, approvals, and status changes.")
    st.markdown("---")

    rows = store.conn.execute(
        "SELECT case_id, event_type, actor, detail, created_at FROM audit_events ORDER BY event_id DESC LIMIT 500"
    ).fetchall()

    if not rows:
        _empty_state_html("No audit events recorded.")
        st.stop()

    df = pd.DataFrame(rows, columns=["Case ID", "Event", "Actor", "Detail", "Timestamp"])
    df["Timestamp"] = df["Timestamp"].apply(_format_timestamp)
    st.dataframe(df, use_container_width=True, hide_index=True)


# --- RAZORPAY ---
elif page == "Razorpay":
    st.markdown("Payment gateway integration and reconciliation status.")
    st.markdown("---")

    adapter = RazorpayAdapter(mock_mode=True)
    key_id = os.environ.get("RAZORPAY_KEY_ID")
    secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    status = "Connected" if key_id else ("Mock" if adapter.mock_mode else "Not configured")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"**Integration Status**: {status}")
    with col2:
        st.markdown(f"**Webhook Secret**: {'Configured' if secret else 'Not configured'}")
    with col3:
        st.markdown(f"**Mode**: {'Mock' if adapter.mock_mode else 'Live'}")

    st.markdown("---")
    st.markdown("### Recent Webhook Activity")

    rows = store.conn.execute(
        "SELECT event_id, event_type, status, received_at, processed_at FROM webhook_events ORDER BY received_at DESC LIMIT 20"
    ).fetchall()

    if rows:
        df = pd.DataFrame(rows, columns=["Event ID", "Type", "Status", "Received", "Processed"])
        df["Received"] = df["Received"].apply(_format_timestamp)
        df["Processed"] = df["Processed"].apply(_format_timestamp)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        _empty_state_html("No webhook events recorded.")


# --- WEBHOOK MONITOR ---
elif page == "Webhook Monitor":
    st.markdown("Real-time payment monitor")
    st.caption("Verified Razorpay webhook traffic only. Uploaded or batch datasets are excluded from this live feed.")
    st.markdown("---")

    st.components.v1.html(
        """
        <script>
        setTimeout(function(){ location.reload(); }, 5000);
        </script>
        """,
        height=0,
        scrolling=False,
    )

    monitor = store.get_webhook_monitor(limit=20)
    events = monitor.get("events", [])
    last_received = monitor.get("last_received_at")
    total_events = monitor.get("total_events", 0)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"**Status**  \n{_status_badge_html('LIVE / CONNECTED', 'success')}", unsafe_allow_html=True)
    with col2:
        st.markdown(f"**Last webhook received**  \n{_format_timestamp(last_received) if last_received else 'No webhook received yet'}")
    with col3:
        st.markdown(f"**Events received**  \n{total_events}")

    if not events:
        _empty_state_html("No verified webhook events processed yet.")
        st.stop()

    table = pd.DataFrame(events)
    if not table.empty:
        table = table[["event_id", "event_type", "payment_id", "entity_id", "amount", "status", "duplicate", "received_at", "processed_at"]]
        table = table.rename(columns={
            "event_id": "Event ID",
            "event_type": "Event Type",
            "payment_id": "Payment ID",
            "entity_id": "Entity ID",
            "amount": "Amount",
            "status": "Processing Status",
            "duplicate": "Duplicate",
            "received_at": "Received At",
            "processed_at": "Processed At",
        })
        table["Received At"] = table["Received At"].apply(_format_timestamp)
        table["Processed At"] = table["Processed At"].apply(_format_timestamp)
        table["Amount"] = table["Amount"].apply(lambda x: f"₹{float(x):,.2f}" if x is not None else "-")
    st.dataframe(table, use_container_width=True, hide_index=True)


# --- SYSTEM HEALTH ---
else:  # page == "System Health"
    st.markdown("Infrastructure and service health status.")
    st.markdown("---")

    db_ok = False
    try:
        db_ok = store.conn.execute("SELECT 1").fetchone() is not None
    except Exception:
        db_ok = False

    adapter = RazorpayAdapter(mock_mode=True)
    rag_ok = False
    try:
        rag_ok = isinstance(retrieve_similar({"case_id": "health_check"}, k=1), list)
    except Exception:
        rag_ok = False

    items = [
        ("API", "Healthy" if db_ok else "Unavailable"),
        ("Database", "Connected" if db_ok else "Disconnected"),
        ("Reconciliation Engine", "Operational"),
        ("RAG Retrieval", "Available" if rag_ok else "Unavailable"),
        ("Razorpay Integration", "Configured" if os.environ.get("RAZORPAY_KEY_ID") or adapter.mock_mode else "Not configured"),
    ]

    cols = st.columns(5)
    for col, (name, status) in zip(cols, items):
        with col:
            variant = "success" if status in {"Healthy", "Connected", "Operational", "Available", "Configured"} else "warning"
            st.markdown(f"**{name}**  \n{_status_badge_html(status, variant)}", unsafe_allow_html=True)


# ============================================================================
# FOOTER
# ============================================================================

st.markdown("---")
st.caption(
    "AI Finance Controller — Financial reconciliation powered by RAG-augmented AI investigation. "
    "No financial decisions without human review."
)
