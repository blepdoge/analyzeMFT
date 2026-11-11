"""The final output (every export format) must carry the full path of each record."""
import csv
import json
import sqlite3

import pytest

from src.analyzeMFT.mft_analyzer import MftAnalyzer
from tests.mft_builder import build_record, data_attr, fn_attr, si_attr

EXPECTED = {
    5: "",                                   # root directory
    40: "\\docs",                            # directory under root
    41: "\\docs\\report.txt",                # file inside the directory
    42: "UnknownParent_99\\lost.txt",        # parent record does not exist
    44: "InvalidRecord_44",                  # slot without a FILE signature
}


def _build_mft(path):
    slots = [bytes(1024)] * 45
    slots[5] = build_record([si_attr(), fn_attr(".", 1, parent=5)], num=5, flags=3)
    slots[40] = build_record([si_attr(), fn_attr("docs", 1, parent=5)], num=40, flags=3)
    slots[41] = build_record([si_attr(), fn_attr("report.txt", 1, parent=40), data_attr(b"x")], num=41, flags=1)
    slots[42] = build_record([si_attr(), fn_attr("lost.txt", 1, parent=99), data_attr(b"x")], num=42, flags=1)
    slots[44] = b"BAAD" + b"\x01" * 1020
    path.write_bytes(b"".join(slots))


def _run(tmp_path, fmt, name):
    mft = tmp_path / "test.mft"
    _build_mft(mft)
    out = tmp_path / name
    analyzer = MftAnalyzer(str(mft), str(out), debug=0, verbosity=0,
                           compute_hashes=False, export_format=fmt)
    import asyncio
    asyncio.run(analyzer.analyze())
    return out


def test_csv_has_full_filepath(tmp_path):
    out = _run(tmp_path, "csv", "out.csv")
    rows = {int(r["Record Number"]): r for r in csv.DictReader(open(out, encoding="utf-8", newline=""))}
    for num, path in EXPECTED.items():
        assert rows[num]["Filepath"] == path, num


def test_json_has_full_filepath(tmp_path):
    out = _run(tmp_path, "json", "out.json")
    rows = {r["recordnum"]: r for r in json.load(open(out, encoding="utf-8"))}
    for num, path in EXPECTED.items():
        assert rows[num]["filepath"] == path, num


def test_sqlite_has_full_filepath(tmp_path):
    out = _run(tmp_path, "sqlite", "out.db")
    conn = sqlite3.connect(out)
    try:
        rows = dict(conn.execute("SELECT record_number, filepath FROM mft_records"))
    finally:
        conn.close()
    for num, path in EXPECTED.items():
        assert rows[num] == path, num


def test_tsk_body_uses_full_path(tmp_path):
    out = _run(tmp_path, "tsk", "out.body")
    names = {int(l.split("|")[2]): l.split("|")[1] for l in open(out, encoding="utf-8").read().splitlines()}
    assert names[41] == "\\docs\\report.txt"
    assert names[42] == "UnknownParent_99\\lost.txt"

