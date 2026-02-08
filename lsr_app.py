# lsr_app.py
from supabase import create_client
from dotenv import load_dotenv

import os
import shutil
import streamlit as st
import pandas as pd
from datetime import date

from lsr_core import normalize_and_import_csv
import plotly.graph_objects as go

# =====================================================
# CSV IMPORT SESSION STATE
# =====================================================

if "show_schema_dialog" not in st.session_state:
    st.session_state.show_schema_dialog = False

if "uploaded_df_temp" not in st.session_state:
    st.session_state.uploaded_df_temp = None


# =====================================================
# SUPABASE SETUP
# =====================================================

load_dotenv()

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_ANON_KEY = st.secrets["SUPABASE_ANON_KEY"]

sb = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Re-attach auth session on every rerun
if "session" in st.session_state:
    sb.auth.set_session(
        st.session_state.session.access_token,
        st.session_state.session.refresh_token
    )

# =====================================================
# METADATA HELPERS
# =====================================================

def load_metadata(project_id):
    res = (
        sb.table("project_metadata")
        .select("data")
        .eq("project_id", project_id)
        .execute()
    )
    if res.data:
        return res.data[0]["data"]
    return {}

def save_metadata(project_id, data):
    sb.table("project_metadata").upsert({
        "project_id": project_id,
        "data": data
    }).execute()

# =====================================================
# AUTH
# =====================================================

def auth_screen():
    st.title("🔐 Login or Register")

    mode = st.selectbox("Action", ["Login", "Register"])
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")

    if st.button(mode):
        try:
            if mode == "Register":
                sb.auth.sign_up({"email": email, "password": password})
                st.success("Check your email to confirm your account.")
            else:
                res = sb.auth.sign_in_with_password({
                    "email": email,
                    "password": password
                })
                st.session_state.user = res.user
                st.session_state.session = res.session
                sb.auth.set_session(
                    res.session.access_token,
                    res.session.refresh_token
                )
                st.rerun()
        except Exception as e:
            st.error(str(e))

def sign_out():
    sb.auth.sign_out()
    st.session_state.clear()
    st.rerun()

# =====================================================
# CONSTANTS
# =====================================================

STAGES = [
    "Study identification",
    "Title/abstract screening",
    "Full-text screening",
    "Data extraction"
]

STAGE_ORDER = [
    "Search results to merge & remove duplicates",
    "Studies included after title/abstract screening",
    "Studies included after full-text screening"
]

# =====================================================
# SANKEY
# =====================================================

def build_sankey_from_counts(ta, ft, de, searches):
    labels, source, target, value = [], [], [], []
    idx = {}

    db_counts = {}
    for s in searches:
        if s["import_stage"] == STAGE_ORDER[0]:
            db = s["database"]
            db_counts[db] = db_counts.get(db, 0) + s["records_raw"]

    for db in db_counts:
        idx[db] = len(labels)
        labels.append(db)

    for name in [
        "Records identified",
        "Title/Abstract screening",
        "Full-text screening",
        "Data extraction"
    ]:
        idx[name] = len(labels)
        labels.append(name)

    for db, n in db_counts.items():
        source.append(idx[db])
        target.append(idx["Records identified"])
        value.append(n)

    source += [
        idx["Records identified"],
        idx["Title/Abstract screening"],
        idx["Full-text screening"]
    ]
    target += [
        idx["Title/Abstract screening"],
        idx["Full-text screening"],
        idx["Data extraction"]
    ]
    value += [ta, ft, de]

    fig = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(label=labels, pad=20, thickness=20),
        link=dict(source=source, target=target, value=value)
    ))

    fig.update_layout(
        font=dict(color="black", size=15),
        height=380,
        margin=dict(l=20, r=20, t=30, b=20)
    )
    return fig

# =====================================================
# STREAMLIT SETUP
# =====================================================

st.set_page_config(
    page_title="Living Systematic Review Manager",
    layout="centered"
)
# =====================================================
# FIX PLOTLY SANKEY DOUBLE TEXT (STREAMLIT SVG SHADOW)
# =====================================================

st.markdown(
    """
    <style>
    svg text {
        text-shadow: none !important;
        stroke: none !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

if "user" not in st.session_state:
    auth_screen()
    st.stop()

st.title("📚 Living Systematic Review Manager")
st.write("Document, standardize, and track database searches for living systematic reviews.")
# =====================================================
# PROJECT DELETION (SUPABASE + LOCAL ARTIFACTS)
# =====================================================

def delete_project(project):
    # 1. delete metadata
    sb.table("project_metadata") \
        .delete() \
        .eq("project_id", project["id"]) \
        .execute()

    # 2. delete project row
    sb.table("projects") \
        .delete() \
        .eq("id", project["id"]) \
        .execute()

    # 3. delete local CSV artifacts
    shutil.rmtree(
        os.path.join("projects", str(project["id"])),
        ignore_errors=True
    )


# =====================================================
# PROJECTS
# =====================================================

st.sidebar.header("📁 Projects")

user_id = st.session_state.user.id

projects = sb.table("projects").select("*").eq("user_id", user_id).execute().data or []

if "current_project" not in st.session_state:
    st.session_state.current_project = None

for p in projects:
    col1, col2 = st.sidebar.columns([0.85, 0.15])

    with col1:
        if st.button(p["name"], key=f"open_{p['id']}"):
            st.session_state.current_project = p
            st.rerun()

    with col2:
        if st.button("🗑", key=f"delete_{p['id']}"):
            delete_project(p)
            st.session_state.current_project = None
            st.rerun()


st.sidebar.markdown("---")
new_project_name = st.sidebar.text_input("New project name")

if st.sidebar.button("➕ Create project") and new_project_name.strip():
    sb.table("projects").insert({
        "user_id": user_id,
        "name": new_project_name.strip()
    }).execute()
    st.rerun()

if st.session_state.current_project is None:
    st.info("👈 Select or create a project.")
    st.stop()

project = st.session_state.current_project
st.subheader(f"📂 Project: {project['name']}")

# =====================================================
# PROJECT ARTIFACT PATHS (LOCAL, PER PROJECT)
# =====================================================

PROJECT_ROOT = "projects"
PROJECT_PATH = os.path.join(PROJECT_ROOT, str(project["id"]))
os.makedirs(PROJECT_PATH, exist_ok=True)

def stage_data_path(project_id, stage):
    mapping = {
        STAGE_ORDER[0]: "records_deduplicated.csv",
        STAGE_ORDER[1]: "records_after_ta.csv",
        STAGE_ORDER[2]: "records_after_ft.csv",
    }
    return os.path.join("projects", str(project_id), mapping[stage])


def count_rows(path):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return len(pd.read_csv(path))
    return 0


# =====================================================
# METADATA INIT
# =====================================================

metadata = load_metadata(project["id"])
if not metadata:
    metadata = {
        "stage_status": {s: "Not started" for s in STAGES},
        "searches": [],
        "study_identification": {"history": [], "current": {}}
    }
    save_metadata(project["id"], metadata)
# =====================================================
# PROJECT PROGRESS DASHBOARD (RESTORED, EXACT)
# =====================================================

st.subheader("📊 Project Status")

# Ensure stage_status exists
metadata.setdefault("stage_status", {})
for stage in STAGES:
    metadata["stage_status"].setdefault(stage, "Not started")

save_metadata(project["id"], metadata)

# -------------------------
# Record counts by stage
# -------------------------

# Study identification = total raw records identified (WITH duplicates)
study_identification_count = sum(
    s.get("records_raw", 0)
    for s in metadata.get("searches", [])
    if s.get("import_stage") == STAGE_ORDER[0]
)

counts = {
    "Study identification": study_identification_count,
    "Title/abstract screening": count_rows(
        stage_data_path(project["id"], STAGE_ORDER[0])
    ),
    "Full-text screening": count_rows(
        stage_data_path(project["id"], STAGE_ORDER[1])
    ),
    "Data extraction": count_rows(
        stage_data_path(project["id"], STAGE_ORDER[2])
    ),
}

# -------------------------
# Table header
# -------------------------

col_stage, col_records, col_status = st.columns([3, 1.2, 4])
with col_stage:
    st.markdown("**Stage**")
with col_records:
    st.markdown("**Records**")
with col_status:
    st.markdown("**Status**")

# -------------------------
# Table rows
# -------------------------

for stage in STAGES:
    col_stage, col_records, col_status = st.columns([3, 1.2, 4])

    with col_stage:
        st.markdown(stage)

    with col_records:
        st.markdown(str(counts.get(stage, 0)))

    with col_status:
        status = metadata["stage_status"][stage]

        status_icon = {
            "Not started": "⚪",
            "In progress": "🟡",
            "Completed": "🟢"
        }[status]

        if st.button(f"{status_icon} {status}", key=f"status_{stage}"):
            next_status = {
                "Not started": "In progress",
                "In progress": "Completed",
                "Completed": "Not started"
            }[status]

            metadata["stage_status"][stage] = next_status
            save_metadata(project["id"], metadata)
            st.rerun()

# =====================================================
# STUDY IDENTIFICATION (FULL LIVING DOCUMENT — RESTORED)
# =====================================================

st.subheader("📘 Study Identification")

metadata = load_metadata(project["id"])
study_id = metadata.setdefault("study_identification", {})
history = study_id.setdefault("history", [])
current = study_id.setdefault("current", {})

with st.expander(
    "Edit study identification (living document)",
    expanded=False
):

    title = st.text_input(
        "Working review title",
        value=current.get("title", "")
    )

    research_question = st.text_area(
        "Primary research question",
        value=current.get("research_question", ""),
        height=80
    )

    population = st.text_input(
        "Population",
        value=current.get("population", "")
    )

    intervention = st.text_input(
        "Intervention / Exposure",
        value=current.get("intervention", "")
    )

    comparator = st.text_input(
        "Comparator (if applicable)",
        value=current.get("comparator", "")
    )

    outcomes = st.text_input(
        "Outcome(s)",
        value=current.get("outcomes", "")
    )

    study_designs = st.text_input(
        "Study designs included",
        value=current.get("study_designs", "")
    )

    inclusion = st.text_area(
        "Inclusion criteria",
        value=current.get("inclusion", ""),
        height=120
    )

    exclusion = st.text_area(
        "Exclusion criteria",
        value=current.get("exclusion", ""),
        height=120
    )

    notes = st.text_area(
        "Notes / rationale (optional)",
        value=current.get("notes", ""),
        height=100
    )

    col_save, col_download = st.columns([1, 1])

    with col_save:
        if st.button("💾 Save new version"):
            new_version = len(history) + 1

            snapshot = {
                "version": new_version,
                "saved_at": date.today().isoformat(),
                "data": {
                    "title": title,
                    "research_question": research_question,
                    "population": population,
                    "intervention": intervention,
                    "comparator": comparator,
                    "outcomes": outcomes,
                    "study_designs": study_designs,
                    "inclusion": inclusion,
                    "exclusion": exclusion,
                    "notes": notes,
                }
            }

            history.append(snapshot)
            study_id["current"] = snapshot["data"]

            # Auto-update Study identification status
            if metadata["stage_status"].get("Study identification") == "Not started":
                metadata["stage_status"]["Study identification"] = "In progress"

            save_metadata(project["id"], metadata)
            st.success(f"Saved version v{new_version}")
            st.rerun()

    with col_download:
        if study_id.get("current") and history:
            current_data = study_id["current"]
            version_num = len(history)
            last_updated = history[-1]["saved_at"]

            export_text = f"""Study Identification & Review Framing
=================================

Working review title:
{current_data.get("title", "")}

Primary research question:
{current_data.get("research_question", "")}

Population:
{current_data.get("population", "")}

Intervention / Exposure:
{current_data.get("intervention", "")}

Comparator:
{current_data.get("comparator", "")}

Outcome(s):
{current_data.get("outcomes", "")}

Study designs included:
{current_data.get("study_designs", "")}

---------------------------------
Inclusion criteria:
{current_data.get("inclusion", "")}

---------------------------------
Exclusion criteria:
{current_data.get("exclusion", "")}

---------------------------------
Notes / rationale:
{current_data.get("notes", "")}

---------------------------------
Version: v{version_num}
Last updated: {last_updated}
"""

            st.download_button(
                label="⬇ Download (TXT)",
                data=export_text,
                file_name=f"{project['name']}_study_identification_v{version_num}.txt",
                mime="text/plain"
            )

# =====================================================
# SEARCH DOCUMENTATION
# =====================================================

st.subheader("🔎 Reference Searches")

csv_purpose = st.selectbox(
    "This CSV contains:",
    [
        "Search results to merge & remove duplicates",
        "Studies included after title/abstract screening",
        "Studies included after full-text screening"
    ]
)

is_db_search_stage = (csv_purpose == STAGE_ORDER[0])

with st.expander("Register reference search", expanded=False):

    if is_db_search_stage:
        database_name = st.text_input(
            "Enter a database searched",
            placeholder="e.g., PubMed"
        )

        search_strategy = st.text_area(
            "Enter the search query you used (verbatim)",
            height=120
        )
    else:
        database_name = None
        search_strategy = None

    if is_db_search_stage:
        c1, c2 = st.columns(2)
        with c1:
            search_start_year = st.number_input(
                "Search start year",
                min_value=1900,
                max_value=date.today().year,
                value=2000
            )
        with c2:
            search_end_year = st.number_input(
                "Search end year",
                min_value=1900,
                max_value=date.today().year,
                value=date.today().year
            )
    else:
        search_start_year = None
        search_end_year = None

    st.markdown("### Upload search results (CSV)")

    uploaded_csv = st.file_uploader(
        "Upload CSV exported directly from the database",
        type=["csv"]
    )

    if uploaded_csv and st.button("📥 Import and register records"):
        if is_db_search_stage:
            if not database_name or not database_name.strip():
                st.error("Database name is required.")
                st.stop()

            if not search_strategy or not search_strategy.strip():
                st.error("Search query is required.")
                st.stop()

        try:
            uploaded_csv.seek(0)
            df_upload = pd.read_csv(uploaded_csv, encoding="utf-8")
        except UnicodeDecodeError:
            uploaded_csv.seek(0)
            df_upload = pd.read_csv(uploaded_csv, encoding="latin-1")

        st.session_state.uploaded_df_temp = df_upload
        st.session_state.show_schema_dialog = True

    # =====================================================
    # SCHEMA MAPPING DIALOG (STEP 2)
    # =====================================================

    if st.session_state.show_schema_dialog and st.session_state.uploaded_df_temp is not None:

        st.markdown("---")
        st.subheader("🧩 Map CSV columns to standardized fields")

        df_upload = st.session_state.uploaded_df_temp
        all_columns = list(df_upload.columns)

        title_col = st.selectbox(
            "Title (required)",
            options=["— Select —"] + all_columns,
            key="map_title"
        )

        authors_col = st.selectbox(
            "Author(s)",
            options=["— None —"] + all_columns,
            key="map_authors"
        )

        journal_col = st.selectbox(
            "Journal / Source",
            options=["— None —"] + all_columns,
            key="map_journal"
        )

        year_col = st.selectbox(
            "Publication year",
            options=["— None —"] + all_columns,
            key="map_year"
        )

        abstract_col = st.selectbox(
            "Abstract",
            options=["— None —"] + all_columns,
            key="map_abstract"
        )

        custom_fields_raw = st.text_area(
            "Additional columns to include (comma-separated)",
            key="map_custom"
        )

        col_confirm, col_cancel = st.columns(2)

        with col_confirm:
            if st.button("✅ Confirm & Import", key="confirm_import"):

                if title_col == "— Select —":
                    st.error("A title column is required.")
                    st.stop()

                def clean(x):
                    return None if x.startswith("—") else x

                rename = {title_col: "title"}

                if clean(authors_col):
                    rename[authors_col] = "authors"
                if clean(journal_col):
                    rename[journal_col] = "journal"
                if clean(year_col):
                    rename[year_col] = "year"
                if clean(abstract_col):
                    rename[abstract_col] = "abstract"

                df_std = df_upload.rename(columns=rename)

                for col in ["authors", "journal", "year", "abstract"]:
                    if col not in df_std.columns:
                        df_std[col] = None

                for c in [x.strip() for x in custom_fields_raw.split(",") if x.strip()]:
                    if c not in df_std.columns:
                        df_std[c] = None

                # -------------------------
                # STAGE ORDER VALIDATION
                # -------------------------

                current_stage_index = STAGE_ORDER.index(csv_purpose)
                current_count = len(df_std)

                if current_stage_index > 0:
                    prev_stage = STAGE_ORDER[current_stage_index - 1]
                    prev_path = stage_data_path(project["id"], prev_stage)

                    if not os.path.exists(prev_path):
                        st.error(f"You must first register a CSV for: '{prev_stage}'.")
                        st.stop()

                    prev_count = count_rows(prev_path)

                    if current_count > prev_count:
                        st.error(
                            f"Invalid CSV: {current_count} records exceed "
                            f"previous stage ({prev_count})."
                        )
                        st.stop()

                # -------------------------
                # SAVE DATA BY STAGE
                # -------------------------

                raw_count = len(df_std)

                if csv_purpose == STAGE_ORDER[0]:
                    added, search_id = normalize_and_import_csv(
                        uploaded_df=df_std,
                        project_csv=stage_data_path(project["id"], csv_purpose),
                        database_name=database_name,
                        search_start_year=search_start_year,
                        search_end_year=search_end_year,
                    )
                    dedup_count = added
                else:
                    df_std.to_csv(
                        stage_data_path(project["id"], csv_purpose),
                        index=False
                    )
                    added = raw_count
                    search_id = None
                    dedup_count = None

                metadata.setdefault("searches", []).append({
                    "search_id": search_id,
                    "database": database_name if is_db_search_stage else None,
                    "search_strategy": search_strategy if is_db_search_stage else None,
                    "search_start_year": search_start_year,
                    "search_end_year": search_end_year,
                    "run_date": date.today().isoformat(),
                    "records_raw": raw_count,
                    "records_deduplicated": dedup_count,
                    "import_stage": csv_purpose,
                })

                save_metadata(project["id"], metadata)

                st.session_state.show_schema_dialog = False
                st.session_state.uploaded_df_temp = None
                st.success(f"Imported {added} records.")
                st.rerun()

        with col_cancel:
            if st.button("❌ Cancel", key="cancel_import"):
                st.session_state.show_schema_dialog = False
                st.session_state.uploaded_df_temp = None
                st.rerun()


# =====================================================
# SEARCH HISTORY
# =====================================================

st.subheader("📜 Reference Search History")

searches = metadata.get("searches", [])

if not searches:
    st.info("No searches documented yet.")
else:
    history_rows = []

    for s in searches:
        history_rows.append({
            "Stage": s.get("import_stage"),
            "Database": s.get("database"),
            "Date": s.get("run_date"),
            "Coverage": (
                f"{s.get('search_start_year')}–{s.get('search_end_year')}"
                if s.get("search_start_year") else None
            ),
            "Records identified": s.get("records_raw"),
            "Search query (verbatim)": s.get("search_strategy"),
        })

    df_history = pd.DataFrame(history_rows)

    st.dataframe(
        df_history,
        use_container_width=True,
        hide_index=True
    )


# =====================================================
# PRISMA SANKEY (RESTORED)
# =====================================================

st.subheader("📊 PRISMA Flow Diagram")

identified = sum(
    s["records_raw"]
    for s in metadata["searches"]
    if s["import_stage"] == STAGE_ORDER[0]
)

ta = count_rows(stage_data_path(project["id"], STAGE_ORDER[0]))
ft = count_rows(stage_data_path(project["id"], STAGE_ORDER[1]))
de = count_rows(stage_data_path(project["id"], STAGE_ORDER[2]))

if metadata["searches"]:
    st.plotly_chart(
        build_sankey_from_counts(ta, ft, de, metadata["searches"]),
        use_container_width=True
    )
# =====================================================
# RECORD SNAPSHOTS BY STAGE
# =====================================================

st.subheader("🗐 Record snapshots by stage")

tab1, tab2, tab3 = st.tabs([
    "After deduplication",
    "After title/abstract screening",
    "After full-text screening"
])

with tab1:
    path = stage_data_path(project["id"], STAGE_ORDER[0])
    if os.path.exists(path):
        st.dataframe(
            pd.read_csv(path),
            use_container_width=True
        )
    else:
        st.info("No deduplicated records uploaded yet.")

with tab2:
    path = stage_data_path(project["id"], STAGE_ORDER[1])
    if os.path.exists(path):
        st.dataframe(
            pd.read_csv(path),
            use_container_width=True
        )
    else:
        st.info("No title/abstract screening results uploaded yet.")

with tab3:
    path = stage_data_path(project["id"], STAGE_ORDER[2])
    if os.path.exists(path):
        st.dataframe(
            pd.read_csv(path),
            use_container_width=True
        )
    else:
        st.info("No full-text screening results uploaded yet.")
