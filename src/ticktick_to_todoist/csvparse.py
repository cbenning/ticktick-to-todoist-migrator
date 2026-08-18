"""Reads a TickTick CSV export into raw row dicts."""

from __future__ import annotations

import csv
from typing import Dict, List

HEADER_FIRST_COLUMN = "Folder Name"


class CsvFormatError(Exception):
    """Raised when the file does not look like a TickTick export."""


def load_records(path: str) -> List[Dict[str, str]]:
    """Skip TickTick's metadata preamble and return the data rows as dicts.

    The preamble contains a quoted field with embedded newlines, so the file
    must be parsed with the csv module rather than read line by line.
    """
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))

    header_index = None
    for index, row in enumerate(rows):
        if row and row[0].strip() == HEADER_FIRST_COLUMN:
            header_index = index
            break

    if header_index is None:
        raise CsvFormatError(
            "Could not find the TickTick header row (expected a column named "
            "'Folder Name'). Is this really a TickTick CSV export?"
        )

    header = [column.strip() for column in rows[header_index]]
    records = []
    for row in rows[header_index + 1:]:
        if not row or all(cell.strip() == "" for cell in row):
            continue
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        records.append(dict(zip(header, row)))
    return records
