import os

import pytest

from ticktick_to_todoist import csvparse

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def test_skips_preamble_and_returns_all_data_rows():
    records = csvparse.load_records(os.path.join(FIXTURES, "sample.csv"))
    assert len(records) == 17


def test_multiline_quoted_preamble_field_does_not_break_parsing():
    # The "Status:" preamble entry is one quoted field spanning three
    # physical lines. A line-oriented reader would mistake those for rows.
    records = csvparse.load_records(os.path.join(FIXTURES, "sample.csv"))
    assert all(r["List Name"] for r in records)


def test_maps_columns_by_header_name():
    records = csvparse.load_records(os.path.join(FIXTURES, "sample.csv"))
    first = records[0]
    assert first["List Name"] == "To-Read"
    assert first["Title"] == "Finish that novel on the nightstand"
    assert first["projectKind"] == "NOTE"


def test_every_record_carries_every_header_column():
    records = csvparse.load_records(os.path.join(FIXTURES, "sample.csv"))
    for record in records:
        assert "parentId" in record


def test_short_rows_are_padded_to_header_length(tmp_path):
    # Every row in sample.csv already has all 25 columns, so it cannot
    # exercise the padding branch. This one is deliberately two columns
    # short of its header.
    source = tmp_path / "short.csv"
    source.write_text(
        '"Date: 2026-08-15+0000"\n'
        '"Folder Name","List Name","Title","taskId","parentId"\n'
        '"","Buy","Milk"\n',
        encoding="utf-8",
    )
    records = csvparse.load_records(str(source))
    assert len(records) == 1
    assert records[0] == {
        "Folder Name": "", "List Name": "Buy", "Title": "Milk",
        "taskId": "", "parentId": "",
    }


def test_missing_header_raises_csv_format_error(tmp_path):
    bad = tmp_path / "nope.csv"
    bad.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(csvparse.CsvFormatError):
        csvparse.load_records(str(bad))


def test_blank_rows_are_dropped(tmp_path):
    source = open(os.path.join(FIXTURES, "sample.csv"), encoding="utf-8").read()
    padded = tmp_path / "padded.csv"
    padded.write_text(source + "\n\n\n", encoding="utf-8")
    assert len(csvparse.load_records(str(padded))) == 17
