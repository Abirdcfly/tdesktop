"""Verify the content-protection patch inside the CI-built Telegram.exe.

Reads the PE straight out of the artifact zip (no 573 MB temp file) and:
  1. reports the build identity (PDB path, PE timestamp, version resource),
  2. locates the COFF symbols for ChannelData::allowsForwarding / flags and
     ChatData::allowsForwarding,
  3. decides whether the patched body ("always true") is in the binary by
     checking whether allowsForwarding still CALLs flags().
"""
import hashlib
import struct
import sys
import zipfile

ZIP = sys.argv[1]
ENTRY = "Telegram.exe"
NEEDLES = [
    b"?allowsForwarding@ChannelData@@",
    b"?flags@ChannelData@@",
    b"?allowsForwarding@ChatData@@",
    b"?flags@ChatData@@",
    b"?allowsForwarding@PeerData@@",
]


def u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def u64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


def main():
    z = zipfile.ZipFile(ZIP)
    fh = z.open(ENTRY)
    info = z.getinfo(ENTRY)
    print(f"zip entry      : {ENTRY}  uncompressed={info.file_size}")

    head = fh.read(0x1000)
    pe_off = u32(head, 0x3C)
    assert head[pe_off:pe_off + 4] == b"PE\0\0", "not a PE"
    coff = pe_off + 4
    machine = u16(head, coff)
    nsec = u16(head, coff + 2)
    ptr_sym = u32(head, coff + 8)
    n_sym = u32(head, coff + 12)
    opt = coff + 20
    magic = u16(head, opt)
    print(f"machine        : {machine:#x}  sections={nsec}  opt_magic={magic:#x}")
    print(f"symbol table   : ptr={ptr_sym:#x} count={n_sym}")
    if not ptr_sym or not n_sym:
        print("-> no COFF symbol table (Release build / stripped)")
        return

    # section table
    sec_off = opt + (240 if magic == 0x20B else 224)
    sections = []
    for i in range(nsec):
        o = sec_off + i * 40
        name = head[o:o + 8].rstrip(b"\0").decode(errors="replace")
        vs, va, rs, raw = u32(head, o + 8), u32(head, o + 12), u32(head, o + 16), u32(head, o + 20)
        sections.append((name, va, vs, raw, rs))
        print(f"  {name:10s} VA={va:#010x} VS={vs:#010x} RAW={raw:#010x}")

    # read symbol table (it may be large; stream it in chunks)
    sym_bytes = bytearray()
    fh.seek(ptr_sym)
    remaining = n_sym * 18
    while remaining:
        chunk = fh.read(min(1 << 22, remaining))
        if not chunk:
            break
        sym_bytes += chunk
        remaining -= len(chunk)
    n = len(sym_bytes) // 18
    print(f"symbols read   : {n}")

    # string table follows the symbol table
    str_off = ptr_sym + n_sym * 18
    fh.seek(str_off)
    str_head = fh.read(4)
    str_size = u32(str_head, 0)
    str_data = fh.read(str_size)

    def sym_name(i):
        rec = sym_bytes[i * 18:(i + 1) * 18]
        nm = rec[0:4]
        if nm[0:4] == b"\0\0\0\0":
            off = u32(nm, 0)
            end = str_data.find(b"\0", off)
            return str_data[off:end].decode(errors="replace")
        return nm.rstrip(b"\0").decode(errors="replace")

    def sym_info(i):
        rec = sym_bytes[i * 18:(i + 1) * 18]
        value = u32(rec, 8)
        sect = struct.unpack_from("<h", rec, 12)[0]
        typ = u16(rec, 14)
        sc = rec[16]
        naux = rec[17]
        aux = value  # for x64, symbol size lives in aux record
        size = None
        if naux and (i + 1) * 18 + 4 <= len(sym_bytes):
            size = u32(sym_bytes, (i + 1) * 18)
        return value, sect, typ, sc, naux, size

    found = {}
    for i in range(n):
        rec = sym_bytes[i * 18:(i + 1) * 18]
        if rec[0:4] != b"\0\0\0\0":
            continue
        name = sym_name(i)
        for needle in NEEDLES:
            if name.startswith(needle.decode()):
                value, sect, typ, sc, naux, size = sym_info(i)
                found[name] = (value, sect, size, i)
                break

    print()
    if not found:
        print("-> target symbols NOT found in symbol table")
        return

    def read_at(rva, count):
        for name, va, vs, raw, rs in sections:
            if va <= rva < va + max(vs, rs):
                off = raw + (rva - va)
                fh.seek(off)
                return off, fh.read(count)
        return None, b""

    for name, (value, sect, size, idx) in sorted(found.items()):
        sname, va, vs, raw, rs = sections[sect - 1]
        rva = va + value
        off, code = read_at(rva, 48)
        print(f"{name}")
        print(f"   section={sname} offset={value:#x} rva={rva:#x} fileoff={off} size={size}")
        print(f"   bytes: {code.hex(' ')}")

    # decide: does ChannelData::allowsForwarding still call ChannelData::flags?
    print()
    for cls in ("ChannelData", "ChatData"):
        af = f"?allowsForwarding@{cls}@@QEBAHXZ"
        fl = f"?flags@{cls}@@QEBA_KXZ"
        if af not in found or fl not in found:
            print(f"{cls}: symbols missing -> cannot decide")
            continue
        a_rva = sections[found[af][1] - 1][1] + found[af][0]
        f_rva = sections[found[fl][1] - 1][1] + found[fl][0]
        size = found[af][2] or 32
        _, code = read_at(a_rva, size + 16)
        calls = []
        for k in range(len(code) - 4):
            if code[k] == 0xE8:  # call rel32
                rel = struct.unpack_from("<i", code, k + 1)[0]
                target = a_rva + k + 5 + rel
                calls.append((k, target))
        hit_flags = [(k, t) for k, t in calls if t == f_rva]
        print(f"{cls}::allowsForwarding  rva={a_rva:#x} size={size}")
        print(f"   calls in body      : {[(hex(k), hex(t)) for k, t in calls]}")
        print(f"   calls flags()      : {[(hex(k), hex(t)) for k, t in hit_flags]}")
        if hit_flags:
            print(f"   => ORIGINAL (still checks the NoForwards flag)")
        else:
            print(f"   => PATCHED (no flags() call -> constant result)")


if __name__ == "__main__":
    main()
