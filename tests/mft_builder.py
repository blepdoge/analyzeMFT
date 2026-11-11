"""Helpers that build small, correctly formed MFT records (with a real update
sequence array) for regression tests. Not a test module."""
import struct

NTFS_TIME = 132500000000000000


def res_attr(attr_type, content, attr_id=0):
    clen = len(content)
    alen = (24 + clen + 7) // 8 * 8
    header = struct.pack('<IIBBHHHIHBB', attr_type, alen, 0, 0, 0x18, 0, attr_id, clen, 0x18, 0, 0)
    return (header + content).ljust(alen, b'\0')


def si_attr(ts=NTFS_TIME):
    return res_attr(0x10, struct.pack('<QQQQ', ts, ts, ts, ts).ljust(48, b'\0'))


def fn_attr(name, namespace=1, parent=5, parent_seq=5, ts=NTFS_TIME, size=1234):
    content = struct.pack('<Q', parent | (parent_seq << 48))
    content += struct.pack('<QQQQ', ts, ts, ts, ts)
    content += struct.pack('<QQIIBB', size, size, 0x20, 0, len(name), namespace)
    content += name.encode('utf-16le')
    return res_attr(0x30, content)


def data_attr(content=b'', attr_id=0):
    return res_attr(0x80, content, attr_id)


def apply_fixup(record, usn=0x0001, stride=512):
    """Store the USN at every stride end and save the originals in the update
    sequence array at offset 48, exactly like NTFS does on disk."""
    count = len(record) // stride + 1
    struct.pack_into('<H', record, 48, usn)
    for i in range(1, count):
        end = i * stride
        record[48 + 2 * i:50 + 2 * i] = record[end - 2:end]
        struct.pack_into('<H', record, end - 2, usn)
    return bytes(record)


def build_record(attrs, num=40, flags=1, size=1024, fixup=True, usn=0x0001):
    record = bytearray(size)
    record[0:4] = b'FILE'
    struct.pack_into('<HH', record, 4, 48, size // 512 + 1)
    first_attr = (48 + 2 * (size // 512 + 1) + 7) // 8 * 8
    struct.pack_into('<HHH', record, 16, 1, 1, first_attr)
    struct.pack_into('<H', record, 22, flags)
    struct.pack_into('<II', record, 28, size, size)
    struct.pack_into('<I', record, 44, num)
    offset = first_attr
    for attr in attrs:
        record[offset:offset + len(attr)] = attr
        offset += len(attr)
    struct.pack_into('<I', record, offset, 0xFFFFFFFF)
    struct.pack_into('<I', record, 24, offset + 8)
    return apply_fixup(record, usn) if fixup else bytes(record)
