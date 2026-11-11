"""Regression tests for MFT record parsing: update sequence (fixup) handling,
$FILE_NAME namespace selection, bounded attribute walking and invalid records."""
import struct

import asyncio
import csv

from src.analyzeMFT.mft_analyzer import MftAnalyzer
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


def _plant_garbage_attribute(raw, offset):
    """Write a fake, well-formed $FILE_NAME attribute after the end marker."""
    data = bytearray(raw)
    junk = fn_attr("GARBAGE_AFTER_END.txt", 1)
    data[offset:offset + len(junk)] = junk
    return bytes(data)


def test_attribute_walk_stops_at_end_marker():
    attrs = [si_attr(), fn_attr("real.txt", 1)]
    raw = build_record(attrs, fixup=False)
    end_marker = 56 + sum(len(a) for a in attrs)
    raw = _plant_garbage_attribute(raw, end_marker + 8)
    rec = MftRecord(raw)
    assert rec.filename == "real.txt"


def test_attribute_walk_bounded_by_used_size_without_end_marker():
    attrs = [si_attr(), fn_attr("real.txt", 1)]
    raw = bytearray(build_record(attrs, fixup=False))
    used = 56 + sum(len(a) for a in attrs)
    raw[used:used + 4] = b'\x00\x00\x00\x00'  # no 0xFFFFFFFF marker
    struct.pack_into('<I', raw, 24, used)
    raw = _plant_garbage_attribute(raw, used)
    rec = MftRecord(bytes(raw))
    assert rec.filename == "real.txt"


def test_invalid_attribute_length_stops_walk_instead_of_scanning_garbage():
    attrs = [si_attr(), fn_attr("first.txt", 1), fn_attr("second.txt", 1)]
    raw = bytearray(build_record(attrs, fixup=False))
    fn_off = 56 + len(attrs[0])
    struct.pack_into('<I', raw, fn_off + 4, 0x7FFFFFF0)  # corrupt length of 1st FN
    rec = MftRecord(bytes(raw))
    assert rec.filename == ""            # walk stopped at the corrupt attribute
    assert 0x30 not in rec.attribute_types
    assert 0x10 in rec.attribute_types


def test_zero_length_attribute_stops_walk():
    attrs = [si_attr(), fn_attr("first.txt", 1)]
    raw = bytearray(build_record(attrs, fixup=False))
    struct.pack_into('<I', raw, 56 + len(attrs[0]) + 4, 0)
    rec = MftRecord(bytes(raw))
    assert rec.attribute_types == {0x10}


def test_parser_exception_in_attribute_does_not_leave_names_unbound(monkeypatch):
    def boom(self, offset):
        raise RuntimeError("boom")
    monkeypatch.setattr(MftRecord, "parse_si_attribute", boom)
    rec = MftRecord(build_record([si_attr(), fn_attr("after.txt", 1)]))
    assert rec.filename == "after.txt"   # walk continued at the next attribute


def _analyze(tmp_path, records, name="mft.bin"):
    mft = tmp_path / name
    mft.write_bytes(b''.join(records))
    out = tmp_path / "out.csv"
    analyzer = MftAnalyzer(str(mft), str(out), export_format="csv")
    asyncio.run(analyzer.analyze())
    return analyzer, out


def test_zeroed_record_between_valid_ones_does_not_clobber_mft_entry(tmp_path):
    records = [
        build_record([si_attr(), fn_attr("$MFT", 3, parent=5)], num=0),
        b'\x00' * 1024,
        build_record([si_attr(), fn_attr("dir", 1, parent=5)], num=2, flags=3),
        b'\x00' * 1024,
        build_record([si_attr(), fn_attr("file.txt", 1, parent=2)], num=4),
    ]
    analyzer, out = _analyze(tmp_path, records)

    assert sorted(analyzer.mft_records) == [0, 2, 4]
    assert analyzer.mft_records[0].filename == "$MFT"
    assert analyzer.stats['skipped_records'] == 2

    rows = {int(r[0]): r for r in list(csv.reader(open(out, encoding='utf-8')))[1:]}
    assert sorted(rows) == [0, 2, 4]
    assert rows[0][1] == "Valid" and rows[0][7] == "$MFT"
    assert analyzer.build_filepath(analyzer.mft_records[4]) == "UnknownParent_5" + chr(92) + "dir" + chr(92) + "file.txt"


def test_invalid_magic_record_keeps_its_position_and_stays_out_of_paths(tmp_path):
    bad = bytearray(build_record([si_attr(), fn_attr("ghost.txt", 1, parent=5)], num=0))
    bad[0:4] = b'BAAD'
    records = [
        build_record([si_attr(), fn_attr("$MFT", 3)], num=0),
        build_record([si_attr(), fn_attr("keep.txt", 1)], num=1),
        bytes(bad),
    ]
    analyzer, out = _analyze(tmp_path, records)

    assert sorted(analyzer.mft_records) == [0, 1, 2]
    assert analyzer.mft_records[0].filename == "$MFT"
    bad_rec = analyzer.mft_records[2]
    assert not bad_rec.is_valid and bad_rec.filename == ""
    assert analyzer.build_filepath(bad_rec) == "InvalidRecord_2"


def test_record_without_header_number_uses_its_position():
    rec = MftRecord(build_record([si_attr(), fn_attr("x.txt", 1)], num=0), record_number=9)
    assert rec.recordnum == 9
    rec = MftRecord(build_record([si_attr(), fn_attr("x.txt", 1)], num=40), record_number=9)
    assert rec.recordnum == 40


def test_generated_mft_parses_cleanly(tmp_path):
    from src.analyzeMFT.test_generator import create_test_mft
    path = tmp_path / "gen.mft"
    create_test_mft(str(path), num_records=40, test_type="normal")
    data = path.read_bytes()
    assert len(data) == 40 * 1024

    parsed = 0
    for i in range(40):
        raw = data[i * 1024:(i + 1) * 1024]
        if not any(raw):
            continue  # reserved, never-used system slots
        rec = MftRecord(raw, record_number=i)
        assert rec.is_valid and rec.fixup_valid
        assert rec.recordnum == i
        assert rec.filename and rec.filename.isprintable()
        assert {0x10, 0x30} <= rec.attribute_types
        assert rec.fn_times['crtime'].dtstr.startswith("20")
        assert rec.si_times['mtime'].dtstr.startswith("20")
        assert rec.get_parent_record_num() == (5 if i < 16 else rec.get_parent_record_num())
        if not rec.flags & 0x02:
            assert rec.data_attribute is not None
        parsed += 1
    assert parsed >= 30
    first = MftRecord(data[:1024])
    assert first.filename == "$MFT"
