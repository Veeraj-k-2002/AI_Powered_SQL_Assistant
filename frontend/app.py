"""
frontend/app.py
────────────────
Main Streamlit entry point.

Layout
──────
  Sidebar  — manage PostgreSQL connections (add / select / delete / test)
  Main     — chat interface for the selected database
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))   # make `utils` importable

import streamlit as st
import pandas as pd
import plotly.express as px
from utils.api_client import (
    list_databases,
    create_database,
    delete_database,
    test_connection,
    send_message,
    get_history,
    clear_history,
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI SQL Chatbot",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Minimal custom CSS ────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    /* Chat bubbles */
    .user-bubble {
        background: #0f62fe;
        color: white;
        padding: 0.6rem 1rem;
        border-radius: 18px 18px 4px 18px;
        max-width: 80%;
        margin-left: auto;
        margin-bottom: 0.5rem;
        word-wrap: break-word;
    }
    .bot-bubble {
        background: #f4f4f4;
        color: #161616;
        padding: 0.6rem 1rem;
        border-radius: 18px 18px 18px 4px;
        max-width: 90%;
        margin-right: auto;
        margin-bottom: 0.5rem;
        word-wrap: break-word;
    }
    /* SQL code box */
    .sql-box {
        background: #1e1e1e;
        color: #d4d4d4;
        font-family: monospace;
        font-size: 0.82rem;
        padding: 0.8rem 1rem;
        border-radius: 8px;
        overflow-x: auto;
        margin: 0.3rem 0 0.8rem 0;
    }
    /* Status badge */
    .badge-success { color: #24a148; font-weight: 600; }
    .badge-error   { color: #da1e28; font-weight: 600; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Session state defaults ────────────────────────────────────────────────────
if "selected_db" not in st.session_state:
    st.session_state.selected_db = None      # dict: {id, name, …}
if "messages" not in st.session_state:
    st.session_state.messages = []           # list of API response dicts
if "show_add_form" not in st.session_state:
    st.session_state.show_add_form = False


# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR — database manager
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("## 🗄️ Database Connections")
    st.divider()

    # Fetch saved connections
    try:
        databases = list_databases()
    except Exception as e:
        st.error(f"Backend unreachable: {e}")
        databases = []

    # ── Select existing connection ────────────────────────────────────────────
    if databases:
        db_options = {db["name"]: db for db in databases}
        selected_name = st.selectbox(
            "Active connection",
            options=list(db_options.keys()),
            index=0,
        )
        selected = db_options[selected_name]

        if (
            st.session_state.selected_db is None
            or st.session_state.selected_db["id"] != selected["id"]
        ):
            # New database selected — reload history
            st.session_state.selected_db = selected
            try:
                hist = get_history(selected["id"])
                st.session_state.messages = hist.get("messages", [])
            except Exception:
                st.session_state.messages = []

        # Action buttons
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔌 Test", use_container_width=True):
                with st.spinner("Testing…"):
                    result = test_connection(selected["id"])
                if result["success"]:
                    st.success(result["message"])
                    if result["tables_found"]:
                        st.caption("Tables: " + ", ".join(result["tables_found"][:8]))
                else:
                    st.error(result["message"])
        with col2:
            if st.button("🗑️ Delete", use_container_width=True):
                delete_database(selected["id"])
                st.session_state.selected_db = None
                st.session_state.messages = []
                st.rerun()

        st.divider()

    else:
        st.info("No connections yet. Add one below.")

    # ── Add new connection form ────────────────────────────────────────────────
    if st.button("➕ Add Connection", use_container_width=True):
        st.session_state.show_add_form = not st.session_state.show_add_form

    if st.session_state.show_add_form:
        with st.form("add_db_form", clear_on_submit=True):
            st.markdown("#### New PostgreSQL Connection")
            name = st.text_input("Friendly name *", placeholder="e.g. Sales DB")
            host = st.text_input("Host *", placeholder="db.example.com")
            port = st.text_input("Port", value="5432")
            database_name = st.text_input("Database name *", placeholder="sales")
            username = st.text_input("Username *", placeholder="readonly_user")
            password = st.text_input("Password *", type="password")
            schema_name = st.text_input("Schema", value="public")

            submitted = st.form_submit_button("Save Connection")
            if submitted:
                if not all([name, host, database_name, username, password]):
                    st.error("Please fill in all required (*) fields.")
                else:
                    with st.spinner("Saving…"):
                        try:
                            create_database({
                                "name": name,
                                "host": host,
                                "port": port,
                                "database_name": database_name,
                                "username": username,
                                "password": password,
                                "schema_name": schema_name,
                            })
                            st.session_state.show_add_form = False
                            st.success("Connection saved!")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN AREA — chat interface
# ══════════════════════════════════════════════════════════════════════════════

if st.session_state.selected_db is None:
    # ── Welcome screen ────────────────────────────────────────────────────────
    st.markdown(
        """
        <div style='text-align:center; padding: 6rem 2rem;'>
            <h1>🤖 AI SQL Chatbot</h1>
            <p style='font-size:1.2rem; color:#555;'>
                Ask plain-English questions about your PostgreSQL database.<br>
                The AI converts them to safe <code>SELECT</code> queries and explains the results.
            </p>
            <p style='color:#888;'>← Add a database connection in the sidebar to get started.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

# ── Header ────────────────────────────────────────────────────────────────────
header_col, clear_col = st.columns([5, 1])
with header_col:
    db = st.session_state.selected_db
    st.markdown(f"### 💬 Chat — `{db['name']}` &nbsp; <span style='font-size:0.85rem; color:#888;'>({db['database_name']} / {db['schema_name']})</span>", unsafe_allow_html=True)
with clear_col:
    if st.button("🧹 Clear", help="Delete all messages for this database"):
        clear_history(db["id"])
        st.session_state.messages = []
        st.rerun()

st.divider()


# ── Render existing messages ──────────────────────────────────────────────────

def render_chart(chart_cfg: dict):
    """Render a Plotly chart from the chart_config returned by the API."""
    if not chart_cfg:
        return

    chart_type = chart_cfg.get("chart_type", "none")
    data = chart_cfg.get("data", {})
    x_vals = data.get("x", [])
    y_vals = data.get("y", [])
    x_label = chart_cfg.get("x_axis", "x")
    y_label = chart_cfg.get("y_axis", "y")

    if chart_type == "none" or not x_vals or not y_vals:
        return

    df_chart = pd.DataFrame({x_label: x_vals, y_label: y_vals})

    if chart_type == "bar":
        fig = px.bar(df_chart, x=x_label, y=y_label)
    elif chart_type == "line":
        fig = px.line(df_chart, x=x_label, y=y_label, markers=True)
    elif chart_type == "scatter":
        fig = px.scatter(df_chart, x=x_label, y=y_label)
    elif chart_type == "pie":
        fig = px.pie(df_chart, names=x_label, values=y_label)
    else:
        fig = px.bar(df_chart, x=x_label, y=y_label)

    fig.update_layout(
        margin=dict(l=20, r=20, t=30, b=20),
        height=350,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_message(msg: dict):
    """Render one chat turn (user bubble + bot response)."""
    # User bubble
    st.markdown(
        f"<div class='user-bubble'>🧑 {msg['user_query']}</div>",
        unsafe_allow_html=True,
    )

    # Bot bubble
    with st.container():
        if msg["status"] == "error":
            st.markdown(
                f"<div class='bot-bubble'>❌ {msg.get('error_message', 'An error occurred.')}</div>",
                unsafe_allow_html=True,
            )
            return

        # Summary text
        summary = msg.get("result_summary") or ""
        st.markdown(f"<div class='bot-bubble'>🤖 {summary}</div>", unsafe_allow_html=True)

        # SQL query (expandable)
        if msg.get("sql_query"):
            with st.expander("🔍 View generated SQL"):
                st.code(msg["sql_query"], language="sql")

        # Result table (expandable)
        table = msg.get("result_table")
        if table:
            with st.expander(f"📋 View data ({len(table)} rows)"):
                st.dataframe(pd.DataFrame(table), use_container_width=True, hide_index=True)

        # Chart
        render_chart(msg.get("chart_config"))

    st.markdown("<br>", unsafe_allow_html=True)


# Render all stored messages
for message in st.session_state.messages:
    render_message(message)

st.divider()

# ── Chat input ────────────────────────────────────────────────────────────────
user_input = st.chat_input(
    placeholder="e.g. What are the top 5 customers by revenue?",
)

if user_input:
    with st.spinner("🤔 Thinking…"):
        try:
            response = send_message(
                database_id=st.session_state.selected_db["id"],
                user_query=user_input,
            )
            st.session_state.messages.append(response)
        except Exception as e:
            st.error(f"Request failed: {e}")

    st.rerun()
