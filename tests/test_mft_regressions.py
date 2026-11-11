"""Regression tests for MFT record parsing: update sequence (fixup) handling,
$FILE_NAME namespace selection, bounded attribute walking and invalid records."""
import struct

from src.analyzeMFT.mft_record import MftRecord
from tests.mft_builder import build_record, si_attr, fn_attr, data_attr


def test_fixup_restores_filename_crossing_sector_boundary():
    # SI (96) + FN header/fixed part puts the name across file offset 510.
    name = "CrossesTheSectorBoundary_" + "x" * 40 + ".txt"
    filler = data_attr(b'p' * 250)
    raw = build_record([si_attr(), filler, fn_attr(name)])
    assert raw[510:512] == b'\x01\x00'  # USN on disk, not name data
    # make sure the name really overlaps offset 510
    name_start = 56 + len(si_attr()) + len(filler) + 24 + 66
    assert name_start < 510 < name_start + 2 * len(name)

    rec = MftRecord(raw)
    assert rec.fixup_valid
    assert rec.filename == name
    assert rec.raw_record[510:512] != b'\x01\x00'
    assert rec.disk_record == raw


def test_fixup_mismatch_marks_record_corrupt_without_crashing():
    raw = bytearray(build_record([si_attr(), fn_attr("a.txt")]))
    raw[1022:1024] = b'\xEE\xEE'  # torn write: USN no longer matches
    rec = MftRecord(bytes(raw))
    assert rec.fixup_valid is False
    assert rec.filename == "a.txt"


def test_invalid_update_sequence_array_does_not_crash():
    raw = bytearray(build_record([si_attr(), fn_attr("a.txt")]))
    struct.pack_into('<H', raw, 6, 0x7FFF)  # absurd update sequence count
    rec = MftRecord(bytes(raw))
    assert rec.fixup_valid is False


def test_record_size_from_header_is_used_for_fixup():
    raw = build_record([si_attr(), fn_attr("big.bin")], size=4096)
    rec = MftRecord(raw)
    assert rec.fixup_valid
    assert len(rec.raw_record) == 4096
    assert rec.filename == "big.bin"
