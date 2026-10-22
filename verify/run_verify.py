#!/usr/bin/env python3
"""Logic-level verification for the content-protection patch.

This does NOT build Telegram Desktop. It compiles the modified functions
*verbatim* (extracted from the real source files) against a minimal stub and
compares the behaviour of the unpatched baseline vs the working tree.

The baseline is a fixed commit (the parent of the first patch commit), not
HEAD, so the A/B comparison stays meaningful after the patch is committed.

The flag constants are parsed from the real headers, so the harness cannot
silently drift from the enum values used by the client.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _find_repo() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (
        here.parent,
        here.parent / "tdesktop",
        here.parent.parent / "tdesktop",
    ):
        if (candidate / "Telegram" / "SourceFiles").is_dir():
            return candidate
    raise SystemExit("tdesktop source tree not found")


REPO = _find_repo()
OUT = Path(__file__).resolve().parent / "out"

# The commit the patch is compared against: the parent of the first patch
# commit ("Ignore peer-wide content protection for verification build").
BASELINE_REF = os.environ.get("VERIFY_BASELINE", "8f49dcadfb^")

TARGETS = [
    {
        "class": "ChannelData",
        "cpp": "Telegram/SourceFiles/data/data_channel.cpp",
        "header": "Telegram/SourceFiles/data/data_channel.h",
    },
    {
        "class": "ChatData",
        "cpp": "Telegram/SourceFiles/data/data_chat.cpp",
        "header": "Telegram/SourceFiles/data/data_chat.h",
    },
]


def find_function(text: str, cls: str) -> tuple[str, int]:
    """Return the verbatim definition of <cls>::allowsForwarding() const."""
    lines = text.splitlines()
    sig = re.compile(rf"^bool\s+{cls}::allowsForwarding\(\)\s*const\s*\{{")
    for i, line in enumerate(lines):
        if sig.match(line.strip()):
            depth = 0
            start = i
            for j in range(i, len(lines)):
                depth += lines[j].count("{") - lines[j].count("}")
                if depth == 0 and j > start:
                    return "\n".join(lines[start : j + 1]), start + 1
                if depth == 0 and j == start and "}" in lines[j]:
                    return lines[j], start + 1
            raise RuntimeError(f"unterminated function {cls}::allowsForwarding")
    raise RuntimeError(f"{cls}::allowsForwarding not found")


def read_flag(header_text: str, cls: str) -> str:
    """Parse the NoForwards initializer from the real enum."""
    m = re.search(r"\bNoForwards\s*=\s*\(([^)]*)\)", header_text)
    if not m:
        raise RuntimeError(f"Flag::NoForwards not found in {cls} header")
    return m.group(1).strip()


def git_show(path: str, ref: str | None = None) -> str:
    ref = ref or BASELINE_REF
    return subprocess.run(
        ["git", "-C", str(REPO), "show", f"{ref}:{path}"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def baseline_available() -> bool:
    """True when the baseline commit is present locally (a shallow checkout
    needs `git fetch --depth=200 origin dev` first)."""
    proc = subprocess.run(
        ["git", "-C", str(REPO), "cat-file", "-e", f"{BASELINE_REF}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def find_compiler(explicit: str | None = None) -> Path | None:
    """Locate a C++ compiler: g++ / clang++ / MSVC cl.exe."""
    if explicit:
        return Path(explicit)
    for name in ("g++", "clang++", "cl"):
        found = shutil.which(name)
        if found:
            return Path(found)
    for root in (
        Path(r"C:\Program Files\Microsoft Visual Studio"),
        Path(r"C:\Program Files (x86)\Microsoft Visual Studio"),
    ):
        if not root.is_dir():
            continue
        for cl in sorted(root.glob("**/VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe")):
            return cl
    return None


def compile_cpp(cc: Path, cpp: Path, exe: Path) -> subprocess.CompletedProcess:
    """Compile a translation unit with either a GNU-style or MSVC compiler."""
    if cc.name.lower().startswith("cl"):
        cmd = [
            str(cc), "/nologo", "/std:c++20", "/EHsc", "/O2", "/W3",
            "/Fo:" + str(cpp.with_suffix(".obj")), str(cpp), "/Fe:" + str(exe),
        ]
    else:
        cmd = [
            str(cc), "-std=c++20", "-O2", "-Wall", "-Wextra",
            "-o", str(exe), str(cpp),
        ]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(cpp.parent))


def build_tu(cls: str, func: str, flag_expr: str, origin: str) -> str:
    return f"""// generated from {origin}
#include <cstdint>
#include <cstdio>
#include <cstdlib>

namespace verify {{

using Flags = unsigned long long;

struct {cls} {{
    Flags _flags = 0;
    Flags flags() const {{ return _flags; }}
    struct Flag {{ static constexpr Flags NoForwards = ({flag_expr}); }};
    bool allowsForwarding() const;  // injected definition below
}};

// ---- verbatim source definition ({origin}), do not edit ----
{func}
// ---- end of verbatim source ----

}}  // namespace verify

int main() {{
    using verify::{cls};
    using Flags = verify::Flags;

    struct Case {{ const char *name; Flags flags; }};
    const Case cases[] = {{
        {{"no flags at all",                 0}},
        {{"unrelated bits only",             (1ULL << 3) | (1ULL << 9)}},
        {{"NoForwards only",                 {cls}::Flag::NoForwards}},
        {{"NoForwards + unrelated bits",     {cls}::Flag::NoForwards | (1ULL << 3) | (1ULL << 9)}},
        {{"every bit except NoForwards",     ~static_cast<Flags>(0) ^ {cls}::Flag::NoForwards}},
        {{"all bits set",                    ~static_cast<Flags>(0)}},
    }};

    for (const auto &c : cases) {{
        {cls} d;
        d._flags = c.flags;
        std::printf("%-32s flags=0x%016llx -> allowsForwarding()=%d\\n",
            c.name,
            static_cast<unsigned long long>(c.flags),
            d.allowsForwarding() ? 1 : 0);
    }}
    return 0;
}}
"""


def main() -> int:
    cc = find_compiler(sys.argv[1] if len(sys.argv) > 1 else None)
    if not cc:
        print("no C++ compiler found (g++/clang++/cl) - skipping", file=sys.stderr)
        return 0
    if not baseline_available():
        print(f"baseline {BASELINE_REF} is not available locally - skipping", file=sys.stderr)
        return 0
    print(f"compiler       : {cc}")
    print(f"baseline ref   : {BASELINE_REF}")
    OUT.mkdir(parents=True, exist_ok=True)

    differs = subprocess.run(
        ["git", "-C", str(REPO), "diff", "--quiet", BASELINE_REF, "--",
         "Telegram/SourceFiles/data/data_channel.cpp",
         "Telegram/SourceFiles/data/data_chat.cpp",
         "Telegram/SourceFiles/history/history_item.cpp"],
    ).returncode != 0
    print(f"source differs from baseline: {differs}\n")

    failures = 0
    for target in TARGETS:
        cls = target["class"]
        old_text = git_show(target["cpp"])
        new_text = (REPO / target["cpp"]).read_text(encoding="utf-8")
        header_text = (REPO / target["header"]).read_text(encoding="utf-8")
        flag_expr = read_flag(header_text, cls)

        print(f"=== {cls} (Flag::NoForwards = ({flag_expr})) ===")
        results = {}
        for variant, text in (("old", old_text), ("new", new_text)):
            func, line = find_function(text, cls)
            src = build_tu(cls, func, flag_expr, f"{target['cpp']}:{line} ({variant})")
            cpp = OUT / f"{cls}_{variant}.cpp"
            exe = OUT / f"{cls}_{variant}.exe"
            cpp.write_text(src, encoding="utf-8")
            proc = compile_cpp(cc, cpp, exe)
            if proc.returncode != 0:
                print(proc.stdout)
                print(proc.stderr)
                return 1
            if proc.stderr.strip():
                print(proc.stderr.strip())
            run = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
            results[variant] = run.stdout.strip().splitlines()
            print(f"\n-- {variant} (compiled from {target['cpp']}:{line}) --")
            print(run.stdout.strip())

        old_rows = [r.split(" -> ")[1] for r in results["old"]]
        new_rows = [r.split(" -> ")[1] for r in results["new"]]
        changed = [i for i, (o, n) in enumerate(zip(old_rows, new_rows)) if o != n]
        print(f"\nrows changed: {len(changed)} of {len(old_rows)}")
        for i in changed:
            label = results["old"][i].split(" flags=")[0].strip()
            print(f"  ! {label}: {old_rows[i]} -> {new_rows[i]}")
        # Expect a difference exactly on the rows that carry NoForwards.
        expected = {2, 3, 5}
        if set(changed) != expected or not differs:
            print(f"UNEXPECTED: changed rows {changed}, expected {sorted(expected)}")
            failures += 1
        else:
            print("OK: behaviour changed only where the peer-wide NoForwards flag is set")
        print()

    print("RESULT:", "PASS" if failures == 0 else "FAIL")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
