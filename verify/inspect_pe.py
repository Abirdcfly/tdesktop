import pefile, sys, os

path = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\Administrator\AppData\Roaming\Telegram Desktop\Telegram.exe"
p = pefile.PE(path, fast_load=True)
print("Machine", hex(p.FILE_HEADER.Machine), "TimeDateStamp", p.FILE_HEADER.TimeDateStamp)
print("PointerToSymbolTable", hex(p.FILE_HEADER.PointerToSymbolTable),
      "NumberOfSymbols", p.FILE_HEADER.NumberOfSymbols)
print("Characteristics", hex(p.FILE_HEADER.Characteristics))
for s in p.sections:
    name = s.Name.decode(errors="replace").rstrip("\x00")
    print(f"{name:10s} VA={s.VirtualAddress:#010x} VS={s.Misc_VirtualSize:#010x} "
          f"RAW={s.PointerToRawData:#010x} RS={s.SizeOfRawData:#010x}")
d = p.OPTIONAL_HEADER.DATA_DIRECTORY
for i, name in [(6, "DEBUG"), (14, "CLR"), (1, "IMPORT"), (0, "EXPORT")]:
    e = d[i]
    print(name, hex(e.VirtualAddress), e.Size)

p.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_DEBUG']])
for dbg in getattr(p, 'DIRECTORY_ENTRY_DEBUG', []):
    print("DEBUG type", dbg.struct.Type, "size", dbg.struct.SizeOfData,
          "addr", hex(dbg.struct.AddressOfRawData), "ptr", hex(dbg.struct.PointerToRawData))
    if dbg.struct.PointerToRawData:
        raw = p.__data__[dbg.struct.PointerToRawData:dbg.struct.PointerToRawData + dbg.struct.SizeOfData]
        print(repr(raw[:300]))

# full symbol table if present
if p.FILE_HEADER.PointerToSymbolTable and p.FILE_HEADER.NumberOfSymbols:
    p.parse_data_directories()
    try:
        p.parse_debug_directory = None
    except Exception:
        pass

# search for known demangled fragments in raw bytes
needles = [b"allowsMediaDownloadControls", b"forbidsSaving", b"hasCopyMediaRestriction",
           b"CanSaveFileWithoutAskingForPath", b"DocumentData::save", b"ChatRestriction"]
data = open(path, "rb").read()
for n in needles:
    idx = data.find(n)
    print("needle", n.decode(), "at", hex(idx) if idx >= 0 else "NOT FOUND",
          "count", data.count(n))
print("size", os.path.getsize(path))
