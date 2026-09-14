"""
Deterministic Excel -> CSV conversion.

This is the actual work. It is pure Python (pandas + openpyxl), has no AI in it,
and is fully unit-testable. Every sheet in the workbook becomes exactly one CSV,
columns/headers preserved.

The public entry point is `convert_excel_to_csv(blob_name)`, which is also the
function the Foundry agent calls as a tool (see app/agent.py).
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

import pandas as pd

from .config import get_settings

logger = logging.getLogger(__name__)

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class SheetResult:
    sheet_name: str          # original sheet name in the workbook
    csv_blob_name: str       # blob name written to the output container
    csv_url: str             # full https URL of the written blob
    rows: int                # data rows (excluding header)
    columns: int             # number of columns


@dataclass
class ConversionResult:
    source_blob: str
    output_container: str
    sheet_count: int
    files: list[SheetResult]

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _sanitize(name: str) -> str:
    """Make a sheet name safe for use inside a blob path."""
    cleaned = _SAFE_CHARS.sub("_", name.strip())
    cleaned = cleaned.strip("._-") or "sheet"
    return cleaned[:120]  # keep well under blob name limits


def _stem(blob_name: str) -> str:
    """'reports/2026/data.xlsx' -> 'data'."""
    return PurePosixPath(blob_name).stem


def _dedupe(name: str, used: set[str]) -> str:
    """Ensure output blob names are unique even if two sheets sanitize equal."""
    candidate, i = name, 1
    while candidate in used:
        root, _, ext = name.partition(".csv")
        candidate = f"{root}_{i}.csv"
        i += 1
    used.add(candidate)
    return candidate


def excel_bytes_to_csvs(
    data: bytes,
    *,
    source_blob: str,
    name_template: str,
) -> list[tuple[str, bytes, str, int, int]]:
    """
    Pure transform: bytes of an .xlsx -> list of
    (csv_blob_name, csv_bytes, sheet_name, rows, columns).

    Separated from I/O so it can be unit-tested without Azure.
    """
    # sheet_name=None -> dict {sheet_name: DataFrame} for every sheet, in order.
    # dtype=object + keep_default_na keeps the CSV faithful to the cell values.
    sheets: dict[str, pd.DataFrame] = pd.read_excel(
        io.BytesIO(data),
        sheet_name=None,
        engine="openpyxl",
        dtype=object,
    )

    stem = _stem(source_blob)
    used: set[str] = set()
    results: list[tuple[str, bytes, str, int, int]] = []

    for sheet_name, df in sheets.items():
        csv_name = name_template.format(stem=stem, sheet=_sanitize(sheet_name))
        csv_name = _dedupe(csv_name, used)

        buf = io.StringIO()
        # index=False -> no pandas row numbers; utf-8-sig -> Excel-friendly BOM.
        df.to_csv(buf, index=False)
        csv_bytes = buf.getvalue().encode("utf-8-sig")

        results.append((csv_name, csv_bytes, sheet_name, int(df.shape[0]), int(df.shape[1])))
        logger.info(
            "Sheet '%s' -> '%s' (%d rows x %d cols).",
            sheet_name, csv_name, df.shape[0], df.shape[1],
        )

    if not results:
        raise ValueError(f"Workbook '{source_blob}' contains no sheets.")

    return results


def convert_excel_to_csv(blob_name: str) -> dict:
    """
    Download `blob_name` from the input container, convert every sheet to its
    own CSV, upload each CSV to the output container, and return a structured
    summary.

    This is the function registered as the Foundry agent's tool. Returning a
    plain dict keeps it JSON-serializable for the agent tool-output contract.

    :param blob_name: Name of the .xlsx / .xlsm blob in the input container,
                      e.g. "data.xlsx" or "reports/2026/data.xlsx".
    :return: dict form of ConversionResult.
    """
    from . import blob_storage  # lazy: keeps the pure transform Azure-free

    s = get_settings()
    logger.info("Converting '%s'.", blob_name)

    raw = blob_storage.download_excel(blob_name)
    parts = excel_bytes_to_csvs(
        raw, source_blob=blob_name, name_template=s.csv_name_template
    )

    files: list[SheetResult] = []
    for csv_name, csv_bytes, sheet_name, rows, cols in parts:
        url = blob_storage.upload_csv(csv_name, csv_bytes)
        files.append(
            SheetResult(
                sheet_name=sheet_name,
                csv_blob_name=(f"{s.output_prefix}{csv_name}" if s.output_prefix else csv_name),
                csv_url=url,
                rows=rows,
                columns=cols,
            )
        )

    result = ConversionResult(
        source_blob=blob_name,
        output_container=s.output_container,
        sheet_count=len(files),
        files=files,
    )
    logger.info("Done: %d sheet(s) -> %d CSV file(s).", result.sheet_count, len(files))
    return result.to_dict()
