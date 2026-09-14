"""
Unit tests for the pure Excel->CSV transform. No Azure needed.

Run:  pytest -q
"""
from __future__ import annotations

import io

import pandas as pd

from app.converter import excel_bytes_to_csvs, _sanitize, _dedupe


def _make_workbook(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
    return buf.getvalue()


def test_three_sheets_make_three_csvs():
    wb = _make_workbook(
        {
            "Sheet1": pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}),
            "Sheet2": pd.DataFrame({"c": [3]}),
            "Sheet3": pd.DataFrame({"d": [4, 5, 6], "e": [7, 8, 9]}),
        }
    )
    parts = excel_bytes_to_csvs(wb, source_blob="data.xlsx", name_template="{stem}__{sheet}.csv")
    assert len(parts) == 3
    names = [p[0] for p in parts]
    assert names == ["data__Sheet1.csv", "data__Sheet2.csv", "data__Sheet3.csv"]


def test_headers_and_rows_preserved():
    wb = _make_workbook({"Data": pd.DataFrame({"name": ["Ana", "Bo"], "age": [30, 25]})})
    parts = excel_bytes_to_csvs(wb, source_blob="people.xlsx", name_template="{stem}__{sheet}.csv")
    csv_name, csv_bytes, sheet, rows, cols = parts[0]
    text = csv_bytes.decode("utf-8-sig")
    assert text.splitlines()[0] == "name,age"   # header row intact
    assert "Ana,30" in text
    assert rows == 2 and cols == 2


def test_sheet_name_sanitized_for_blob_path():
    # Spaces and parentheses are legal in Excel sheet names but must be cleaned
    # up for a blob path. (Excel forbids \ / ? * [ ] : so we don't test those.)
    wb = _make_workbook({"Q1 2026 (draft)": pd.DataFrame({"x": [1]})})
    parts = excel_bytes_to_csvs(wb, source_blob="rep.xlsx", name_template="{stem}__{sheet}.csv")
    assert parts[0][0] == "rep__Q1_2026_draft.csv"


def test_duplicate_sanitized_names_are_deduped():
    # Two distinct, Excel-legal sheet names that sanitize to the same token.
    wb = _make_workbook(
        {"Data 1": pd.DataFrame({"x": [1]}), "Data#1": pd.DataFrame({"y": [2]})}
    )
    parts = excel_bytes_to_csvs(wb, source_blob="d.xlsx", name_template="{stem}__{sheet}.csv")
    names = [p[0] for p in parts]
    assert names == ["d__Data_1.csv", "d__Data_1_1.csv"]
    assert len(set(names)) == len(names)  # all unique


def test_sanitize_helpers():
    # A maximal run of unsafe characters collapses to a single underscore.
    assert _sanitize("Q1 / 2026") == "Q1_2026"
    used: set[str] = set()
    assert _dedupe("f.csv", used) == "f.csv"
    assert _dedupe("f.csv", used) == "f_1.csv"
