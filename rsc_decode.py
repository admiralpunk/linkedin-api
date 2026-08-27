#!/usr/bin/env python3
"""
rsc_decode.py — decode a LinkedIn SDUI / React Server Component (RSC) stream.

LinkedIn's profile pages no longer return clean JSON. They return an RSC wire
stream: numbered chunks that serialize a React element tree. The actual data is
lazy — each leaf is a `componentKey` slot with initialContent:$undefined that
must be resolved with a SEPARATE follow-up request.

This tool takes such a stream (raw text, or the base64 `data:` URI you can copy
from DevTools) and:
  * splits the numbered chunks (N:I[...] imports, N:[...] trees, N:null)
  * pretty-prints each chunk's JSON
  * extracts every `componentKey` (the lazy slots you'd resolve next)
  * extracts every `data-sdui-component` and `observabilityIdentifier`

FOR EDUCATIONAL USE. Understanding a wire format is not the same as scraping —
keep any live use to your own account and tiny volume.

Usage:
    python rsc_decode.py stream.txt              # from a file
    python rsc_decode.py --b64 "MTpJWy..."       # from base64 text
    python rsc_decode.py --b64 "data:application/octet-stream;base64,MTpJ..."
    type stream.txt | python rsc_decode.py -     # from stdin
"""

import base64
import json
import re
import sys


def load_input(argv) -> str:
    """Return the raw RSC text from a file, stdin, or a base64 blob."""
    if "--b64file" in argv:
        # base64 blob stored in a file (avoids shell command-line length limits)
        with open(argv[argv.index("--b64file") + 1], encoding="utf-8") as f:
            blob = f.read().strip()
        if "base64," in blob:
            blob = blob.split("base64,", 1)[1]
        return base64.b64decode(blob).decode("utf-8", "replace")
    if "--b64" in argv:
        blob = argv[argv.index("--b64") + 1]
        # tolerate a full data: URI prefix
        if "base64," in blob:
            blob = blob.split("base64,", 1)[1]
        return base64.b64decode(blob).decode("utf-8", "replace")
    src = argv[1]
    if src == "-":
        return sys.stdin.read()
    with open(src, encoding="utf-8") as f:
        return f.read()


def split_chunks(text: str):
    """
    Split the stream into (id, kind, payload) tuples.

    Each line looks like `<id>:<body>`. Body starts with `I[` for a module
    import row, or is a JSON value (the element tree, or a scalar like null).
    A single logical chunk can wrap across lines, so we re-join on the
    leading `<digits>:` marker.
    """
    chunks = []
    current_id = None
    buf = []
    line_re = re.compile(r"^(\d+):(.*)$", re.S)

    for line in text.splitlines():
        m = line_re.match(line)
        if m:
            if current_id is not None:
                chunks.append((current_id, "".join(buf)))
            current_id = m.group(1)
            buf = [m.group(2)]
        else:
            buf.append("\n" + line)
    if current_id is not None:
        chunks.append((current_id, "".join(buf)))

    out = []
    for cid, body in chunks:
        body = body.strip()
        if body.startswith("I["):
            kind, payload = "import", body[1:]  # strip the leading I
        else:
            kind, payload = "tree", body
        out.append((cid, kind, payload))
    return out


def walk(node, keys, sdui, obs):
    """Recursively collect componentKeys and sdui identifiers from the tree."""
    if isinstance(node, dict):
        for k in ("componentKey", "componentkey"):
            if k in node and isinstance(node[k], str):
                keys.add(node[k])
        if "data-sdui-component" in node:
            sdui.add(node["data-sdui-component"])
        if "observabilityIdentifier" in node:
            obs.add(node["observabilityIdentifier"])
        for v in node.values():
            walk(v, keys, sdui, obs)
    elif isinstance(node, list):
        for v in node:
            walk(v, keys, sdui, obs)


def main() -> int:
    argv = sys.argv
    if len(argv) < 2:
        print(__doc__)
        return 2

    # optional: --out FILE tees all output to a file as well as stdout
    out_path = None
    if "--out" in argv:
        i = argv.index("--out")
        out_path = argv[i + 1] if i + 1 < len(argv) else "response.txt"

    lines = []

    def emit(s: str = "") -> None:
        print(s)
        lines.append(s)

    text = load_input(argv)
    chunks = split_chunks(text)

    keys, sdui, obs = set(), set(), set()

    emit(f"=== {len(chunks)} chunks ===\n")
    for cid, kind, payload in chunks:
        emit(f"--- chunk {cid} ({kind}) ---")
        try:
            val = json.loads(payload)
            emit(json.dumps(val, indent=2, ensure_ascii=False))
            walk(val, keys, sdui, obs)
        except json.JSONDecodeError:
            emit(payload)  # scalar like `null` or non-JSON
        emit()

    emit("=== extracted ===")
    emit(f"\nsdui components ({len(sdui)}):")
    for s in sorted(sdui):
        emit(f"  {s}")
    emit(f"\nobservability sections ({len(obs)}):")
    for o in sorted(obs):
        emit(f"  {o}")
    emit(f"\ncomponentKeys — the lazy slots to resolve next ({len(keys)}):")
    for k in sorted(keys):
        emit(f"  {k}")

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"\n[written to {out_path}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
