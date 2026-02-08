# lsr_core.py

import os
import pandas as pd
from datetime import date


def normalize_colname(col):
    return col.lower().replace(" ", "").replace("_", "")


COLUMN_ALIASES = {
    "title": {
        "title", "articletitle", "documenttitle",
        "publicationtitle", "itemtitle", "ti"
    },
    "abstract": {
        "abstract", "abstracttext", "abstractnote",
        "summary", "description", "ab"
    },
    "journal": {
        "journal", "journal/book", "source", "sourcetitle",
        "publicationname", "containertitle", "so"
    },
    "year": {
        "year", "publicationyear", "py", "date", "issued"
    }
}


def resolve_bibliographic_columns(df):
    normalized = {normalize_colname(c): c for c in df.columns}
    resolved = {}

    for canonical, aliases in COLUMN_ALIASES.items():
        resolved[canonical] = next(
            (normalized[a] for a in aliases if a in normalized),
            None
        )

    return resolved

# =========================
# CANONICAL CSV SCHEMA
# =========================

FINAL_COLUMNS = [
    "database",
    "title",
    "journal",
    "year",
    "abstract",
    "abstract_source",
    "search_id",
    "search_start_year",
    "search_end_year",
    "run_date",
]

# =========================
# UPDATE / APPEND RECORDS
# =========================

def update_lsr_database(
    records,
    project_csv,
    search_start_year,
    search_end_year,
):
    run_date = date.today().isoformat()

    # ---------- LOAD EXISTING ----------
    if os.path.exists(project_csv) and os.path.getsize(project_csv) > 0:
        df_old = pd.read_csv(project_csv)

        # Backward compatibility
        if "search_id" not in df_old.columns and "search_round" in df_old.columns:
            df_old = df_old.rename(columns={"search_round": "search_id"})

        next_search_id = int(df_old["search_id"].max()) + 1
        existing_titles = set(
            df_old["title"].astype(str).str.lower().str.strip().dropna()
        )
    else:
        df_old = pd.DataFrame(columns=FINAL_COLUMNS)
        next_search_id = 1
        existing_titles = set()

    new_rows = []

    for r in records:
        title = str(r.get("title", "")).strip()
        if not title:
            continue

        if title.lower() in existing_titles:
            continue

        new_rows.append({
            "database": r.get("database"),
            "title": title,
            "journal": r.get("journal"),
            "year": r.get("year"),
            "abstract": r.get("abstract"),
            "abstract_source": r.get("abstract_source", "csv_import"),
            "search_id": next_search_id,
            "search_start_year": search_start_year,
            "search_end_year": search_end_year,
            "run_date": run_date,
        })

    if new_rows:
        df_new = pd.DataFrame(new_rows, columns=FINAL_COLUMNS)
        df_all = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df_all = df_old

    df_all.to_csv(project_csv, index=False)
    return len(new_rows), next_search_id


# =========================
# NORMALIZE CSV IMPORT
# =========================

def normalize_and_import_csv(
    uploaded_df,
    project_csv,
    database_name,
    search_start_year,
    search_end_year,
):
    records = []

    for _, row in uploaded_df.iterrows():
        records.append({
            "database": database_name,
            "title": row.get("title"),
            "journal": row.get("journal"),
            "year": row.get("year"),
            "abstract": row.get("abstract"),
            "abstract_source": "csv_import",
        })

    return update_lsr_database(
        records=records,
        project_csv=project_csv,
        search_start_year=search_start_year,
        search_end_year=search_end_year,
    )
