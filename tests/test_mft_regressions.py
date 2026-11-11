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


def test_win32_name_preferred_over_dos_name_either_order():
    long_name, dos_name = "LongFileName_document.txt", "LONGFI~1.TXT"
    for attrs in ([fn_attr(long_name, 1), fn_attr(dos_name, 2)],
                  [fn_attr(dos_name, 2), fn_attr(long_name, 1)]):
        rec = MftRecord(build_record([si_attr()] + attrs))
        assert rec.filename == long_name


def test_win32_and_dos_and_posix_names_beat_dos_only():
    for ns in (0, 1, 3):
        rec = MftRecord(build_record([si_attr(), fn_attr("SHORT~1.TXT", 2), fn_attr("real name.txt", ns)]))
        assert rec.filename == "real name.txt"


def test_dos_name_kept_when_it_is_the_only_name():
    rec = MftRecord(build_record([si_attr(), fn_attr("ONLY~1.TXT", 2, parent=7)]))
    assert rec.filename == "ONLY~1.TXT"
    assert rec.get_parent_record_num() == 7
