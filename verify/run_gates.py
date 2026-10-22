#!/usr/bin/env python3
"""Second-stage verification: compile the real download gates verbatim.

Stage 1 (run_verify.py) proved that ChannelData/ChatData::allowsForwarding()
flips to true. This script pulls the *actual* gate functions that decide whether
the save/download UI is offered, plus the real PeerData dispatch shape, and
evaluates them against realistic scenarios:

  A. protected channel, per-message noforwards NOT set  (the real-world case)
  B. protected channel, per-message noforwards set      (bot-level protection)
  C. protected supergroup (ChatData, different flag bit)
  D. unprotected channel                                (control)
  E. protected channel + TTL media                     (must stay blocked)

Every function body is extracted verbatim from the working tree / the baseline
commit (VERIFY_BASELINE, default: the parent of the first patch commit).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import importlib.util

_spec = importlib.util.spec_from_file_location("rv", HERE / "run_verify.py")
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

OUT = HERE / "out" / "gates"

FUNCS = [
    ("history_item.cpp", "HistoryItem::forbidsForward"),
    ("history_item.cpp", "HistoryItem::forbidsSaving"),
    ("history_item.cpp", "HistoryItem::allowsMediaDownloadControls"),
    ("history_inner_widget.cpp", "HistoryInner::hasCopyRestriction"),
    ("history_inner_widget.cpp", "HistoryInner::hasCopyMediaRestriction"),
    ("media_view_overlay_widget.cpp", "OverlayWidget::hasCopyMediaRestriction"),
    ("window_session_controller.cpp", "HasSavingRestriction"),
    ("history_view_list_widget.cpp", "CopyRestrictionTypeFor"),
    ("history_view_list_widget.cpp", "CopyMediaRestrictionTypeFor"),
]

PATHS = {
    "history_item.cpp": "Telegram/SourceFiles/history/history_item.cpp",
    "history_inner_widget.cpp": "Telegram/SourceFiles/history/history_inner_widget.cpp",
    "media_view_overlay_widget.cpp": "Telegram/SourceFiles/media/view/media_view_overlay_widget.cpp",
    "window_session_controller.cpp": "Telegram/SourceFiles/window/window_session_controller.cpp",
    "history_view_list_widget.cpp": "Telegram/SourceFiles/history/view/history_view_list_widget.cpp",
}

RET = r"(?:\[\[nodiscard\]\]\s*)?(?:bool|CopyRestrictionType)"


def find_def(text: str, qualified: str) -> tuple[str, int]:
    lines = text.splitlines()
    pat = re.compile(rf"^{RET}\s+{re.escape(qualified)}\(")
    for i, line in enumerate(lines):
        if pat.match(line.strip()):
            depth = 0
            started = False
            for j in range(i, len(lines)):
                opens = lines[j].count("{")
                closes = lines[j].count("}")
                if opens:
                    started = True
                depth += opens - closes
                if started and depth == 0:
                    return "\n".join(lines[i : j + 1]), i + 1
            raise RuntimeError(f"unterminated {qualified}")
    raise RuntimeError(f"{qualified} not found")


def flag_value(path: str, name: str) -> str:
    text = (rv.REPO / path).read_text(encoding="utf-8")
    m = re.search(rf"\b{name}\s*=\s*\(([^)]*)\)", text)
    if not m:
        raise RuntimeError(f"{name} not found in {path}")
    return m.group(1).strip()


def build(srcs: dict[str, str], header: str) -> str:
    def fn(key: str) -> str:
        return srcs[key]

    return f"""// generated: gate functions are verbatim from the real sources
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace v {{

using Flags = unsigned long long;

struct MessageFlag {{
    static constexpr Flags NoForwards = ({flag_value('Telegram/SourceFiles/data/data_types.h', 'NoForwards')});
}};

// ---- real ChannelData::allowsForwarding ----------------------------------
struct ChannelData {{
    Flags _flags = 0;
    Flags flags() const {{ return _flags; }}
    struct Flag {{ static constexpr Flags NoForwards = ({flag_value('Telegram/SourceFiles/data/data_channel.h', 'NoForwards')}); }};
    bool allowsForwarding() const;
}};
{fn('ChannelData::allowsForwarding')}
// --------------------------------------------------------------------------

// ---- real ChatData::allowsForwarding -------------------------------------
struct ChatData {{
    Flags _flags = 0;
    Flags flags() const {{ return _flags; }}
    struct Flag {{ static constexpr Flags NoForwards = ({flag_value('Telegram/SourceFiles/data/data_chat.h', 'NoForwards')}); }};
    bool allowsForwarding() const;
}};
{fn('ChatData::allowsForwarding')}
// --------------------------------------------------------------------------

template <typename T>
struct not_null {{
    T ptr;
    not_null(T p) : ptr(p) {{}}
    T operator->() const {{ return ptr; }}
    operator T() const {{ return ptr; }}
}};

enum class CopyRestrictionType : char {{ None, Group, Channel, User }};

// Mirrors PeerData::allowsForwarding() dispatch in data_peer.cpp.
struct PeerData {{
    enum class Kind {{ User, Channel, Chat }};
    Kind kind = Kind::Channel;
    bool userAllows = true;
    ChannelData channel;
    ChatData chat;

    bool allowsForwarding() const {{
        if (kind == Kind::User) {{
            return userAllows;
        }} else if (kind == Kind::Channel) {{
            return channel.allowsForwarding();
        }}
        return chat.allowsForwarding();
    }}
    bool isUser() const {{ return kind == Kind::User; }}
    bool isBroadcast() const {{ return kind == Kind::Channel; }}
}};

struct MediaStub {{
    int _ttl = 0;
    int ttlSeconds() const {{ return _ttl; }}
    int *invoice() const {{ return nullptr; }}
    bool allowsForward() const {{ return true; }}
}};

bool HasExtendedMedia(int) {{ return false; }}

struct HistoryStub {{ PeerData *peer = nullptr; }};

struct HistoryItem {{
    Flags _flags = 0;
    MediaStub *_media = nullptr;
    HistoryStub *_history = nullptr;
    HistoryStub *history() const {{ return _history; }}
    bool forbidsForward() const;
    bool forbidsSaving() const;
    bool allowsMediaDownloadControls() const;
}};
{fn('HistoryItem::forbidsForward')}
{fn('HistoryItem::forbidsSaving')}
{fn('HistoryItem::allowsMediaDownloadControls')}

struct HistoryInner {{
    PeerData *_peer = nullptr;
    bool hasCopyRestriction(HistoryItem *item) const;
    bool hasCopyMediaRestriction(not_null<HistoryItem*> item) const;
}};
{fn('HistoryInner::hasCopyRestriction')}
{fn('HistoryInner::hasCopyMediaRestriction')}

struct StoryStub {{
    bool call() const {{ return false; }}
    bool canDownloadIfPremium() const {{ return true; }}
    bool canDownloadChecked() const {{ return true; }}
}};
struct StoriesStub {{ StoryStub *story() const {{ return nullptr; }} }};
struct OverlayWidget {{
    StoriesStub *_stories = nullptr;
    HistoryStub *_history = nullptr;
    HistoryItem *_message = nullptr;
    bool hasCopyMediaRestriction(bool skipPremiumCheck) const;
}};
{fn('OverlayWidget::hasCopyMediaRestriction')}

{fn('HasSavingRestriction')}

{fn('CopyRestrictionTypeFor')}
{fn('CopyMediaRestrictionTypeFor')}

const char *EnumName(CopyRestrictionType v) {{
    switch (v) {{
    case CopyRestrictionType::None: return "None";
    case CopyRestrictionType::Group: return "Group";
    case CopyRestrictionType::Channel: return "Channel";
    case CopyRestrictionType::User: return "User";
    }}
    return "?";
}}

}}  // namespace v

int main() {{
    using namespace v;

    struct Scenario {{
        const char *name;
        const char *kind;
        Flags peerFlags;
        Flags itemFlags;
        int ttl;
    }};
    const Scenario scenarios[] = {{
        {{"protected channel, no per-message flag", "channel",
            ChannelData::Flag::NoForwards, 0, 0}},
        {{"protected channel + per-message flag", "channel",
            ChannelData::Flag::NoForwards, MessageFlag::NoForwards, 0}},
        {{"protected supergroup (ChatData bit)", "chat",
            ChatData::Flag::NoForwards, 0, 0}},
        {{"unprotected channel", "channel", 0, 0, 0}},
        {{"protected channel + TTL media", "channel",
            ChannelData::Flag::NoForwards, 0, 60}},
    }};

    for (const auto &s : scenarios) {{
        PeerData peer;
        peer.kind = (std::strcmp(s.kind, "chat") == 0)
            ? PeerData::Kind::Chat
            : PeerData::Kind::Channel;
        if (peer.kind == PeerData::Kind::Chat) {{
            peer.chat._flags = s.peerFlags;
        }} else {{
            peer.channel._flags = s.peerFlags;
        }}
        MediaStub media;
        media._ttl = s.ttl;
        HistoryStub history{{&peer}};
        HistoryItem item;
        item._flags = s.itemFlags;
        item._media = &media;
        item._history = &history;
        HistoryInner inner{{&peer}};
        OverlayWidget overlay;
        overlay._history = &history;
        overlay._message = &item;

        std::printf("SCENARIO\\t%s\\n", s.name);
        std::printf("GATE\\tallowsMediaDownloadControls\\t%d\\n", item.allowsMediaDownloadControls() ? 0 : 1);
        std::printf("GATE\\thasCopyRestriction\\t%d\\n", inner.hasCopyRestriction(&item) ? 1 : 0);
        std::printf("GATE\\thasCopyMediaRestriction\\t%d\\n", inner.hasCopyMediaRestriction(&item) ? 1 : 0);
        std::printf("GATE\\toverlayHasCopyMediaRestriction\\t%d\\n", overlay.hasCopyMediaRestriction(true) ? 1 : 0);
        std::printf("GATE\\tHasSavingRestriction\\t%d\\n", HasSavingRestriction(&item) ? 1 : 0);
        std::printf("GATE\\tCopyRestrictionTypeFor\\t%s\\n", EnumName(CopyRestrictionTypeFor(&peer, &item)));
        std::printf("GATE\\tCopyMediaRestrictionTypeFor\\t%s\\n", EnumName(CopyMediaRestrictionTypeFor(&peer, &item)));
    }}
    return 0;
}}
"""


# 1 = blocked, 0 = download offered. Values are the *desired* (patched) result.
EXPECTED_NEW = {
    "protected channel, no per-message flag": [0, 0, 0, 0, 0, "None", "None"],
    "protected channel + per-message flag": [0, 0, 0, 0, 0, "None", "None"],
    "protected supergroup (ChatData bit)": [0, 0, 0, 0, 0, "None", "None"],
    "unprotected channel": [0, 0, 0, 0, 0, "None", "None"],
    # TTL media stays protected: the patch must not open self-destructing media.
    "protected channel + TTL media": [1, 0, 1, 1, 1, "None", "None"],
}
EXPECTED_OLD = {
    "protected channel, no per-message flag": [1, 1, 1, 1, 1, "Channel", "Channel"],
    "protected channel + per-message flag": [1, 1, 1, 1, 1, "Channel", "Channel"],
    "protected supergroup (ChatData bit)": [1, 1, 1, 1, 1, "Group", "Group"],
    "unprotected channel": [0, 0, 0, 0, 0, "None", "None"],
    "protected channel + TTL media": [1, 1, 1, 1, 1, "Channel", "Channel"],
}
GATES = [
    "allowsMediaDownloadControls",
    "hasCopyRestriction",
    "hasCopyMediaRestriction",
    "overlayHasCopyMediaRestriction",
    "HasSavingRestriction",
    "CopyRestrictionTypeFor",
    "CopyMediaRestrictionTypeFor",
]


def parse(output: str) -> dict[str, list[str]]:
    table: dict[str, list[str]] = {}
    current = None
    for line in output.splitlines():
        parts = line.split("\t")
        if parts[0] == "SCENARIO":
            current = parts[1]
            table[current] = []
        elif parts[0] == "GATE":
            value = parts[2]
            table[current].append(int(value) if value in ("0", "1") else value)
    return table


def main() -> int:
    cc = rv.find_compiler(sys.argv[1] if len(sys.argv) > 1 else None)
    if not cc:
        print("no C++ compiler found (g++/clang++/cl) - skipping", file=sys.stderr)
        return 0
    if not rv.baseline_available():
        print(f"baseline {rv.BASELINE_REF} is not available locally - skipping", file=sys.stderr)
        return 0
    print(f"compiler       : {cc}")
    print(f"baseline ref   : {rv.BASELINE_REF}")
    OUT.mkdir(parents=True, exist_ok=True)
    failures = 0

    for variant in ("old", "new"):
        srcs = {}
        for filename, qualified in FUNCS:
            path = PATHS[filename]
            text = rv.git_show(path) if variant == "old" else (rv.REPO / path).read_text(encoding="utf-8")
            body, line = find_def(text, qualified)
            srcs[qualified] = body
            if variant == "new":
                print(f"  extracted {qualified} from {filename}:{line}")
        for name, path in (
            ("ChannelData::allowsForwarding", "Telegram/SourceFiles/data/data_channel.cpp"),
            ("ChatData::allowsForwarding", "Telegram/SourceFiles/data/data_chat.cpp"),
        ):
            text = rv.git_show(path) if variant == "old" else (rv.REPO / path).read_text(encoding="utf-8")
            srcs[name], _ = find_def(text, name)

        cpp = OUT / f"gates_{variant}.cpp"
        exe = OUT / f"gates_{variant}.exe"
        cpp.write_text(build(srcs, variant), encoding="utf-8")
        proc = rv.compile_cpp(cc, cpp, exe)
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr)
            return 1
        if proc.stderr.strip():
            print(proc.stderr.strip())

        run = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
        table = parse(run.stdout)
        expected = EXPECTED_OLD if variant == "old" else EXPECTED_NEW

        print(f"\n=== {variant} ===")
        print(f"{'scenario':40} " + " ".join(f"{g[:14]:>14}" for g in GATES))
        for scenario, values in table.items():
            print(f"{scenario:40} " + " ".join(f"{v:>14}" for v in values))
            if values != expected[scenario]:
                print(f"  EXPECTED {expected[scenario]}")
                failures += 1

    print("\nRESULT:", "PASS" if failures == 0 else "FAIL")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
