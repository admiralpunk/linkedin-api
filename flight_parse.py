#!/usr/bin/env python3
"""
flight_parse.py — a complete parser for LinkedIn's RSC (React Flight) wire format.

Where rsc_decode.py just splits chunks, this RESOLVES the Flight reference
grammar so the element tree is fully materialized with data inline. That is the
"complete syntax": every `$L`, `$`, `$Q`, `$n`, `$undefined`, `$Symbol`, and
`$row:path` reference is dereferenced.

Flight grammar (as observed in real LinkedIn responses):
    <hexid>:<payload>\n            one row per logical value
    payload = I[id,[deps],name]     client module import  -> {"__module__": name}
            | JSON value            model (element tree / object / array)
            | scalar                e.g. null
    reference tokens inside models:
        "$"            React element marker (slot 0 of ["$", type, key, props])
        "$L<hex>"      lazy   -> value of row <hex>
        "$<hex>"       direct -> value of row <hex>
        "$Q<hex>"      Map    -> value of row <hex>
        "$S<name>"     Symbol -> "Symbol(<name>)"
        "$n<digits>"   BigInt -> int
        "$undefined"   -> None
        "$<hex>:<path>"-> navigate into row <hex> by colon-separated keys/indices
        "$$..."        -> literal string that began with a single '$' (unescape)
    ($type / $case are NOT Flight tokens; they are LinkedIn protobuf keys.)

FOR EDUCATIONAL USE.

Usage:
    python flight_parse.py response.txt              # decoded (tee'd) RSC text
    python flight_parse.py --b64file blob.b64        # raw base64 stream
    python flight_parse.py response.txt --out resolved.json
"""

import base64
import json
import re
import sys

ROW_RE = re.compile(r"^([0-9a-f]+):(.*)$", re.S)


def load(argv):
    if "--b64file" in argv:
        blob = open(argv[argv.index("--b64file") + 1], encoding="utf-8").read().strip()
        if "base64," in blob:
            blob = blob.split("base64,", 1)[1]
        return base64.b64decode(blob).decode("utf-8", "replace")
    path = [a for a in argv[1:] if not a.startswith("--")][0]
    return open(path, encoding="utf-8").read()


def split_rows(text):
    """Return {hexid: raw_payload_string}. Rejoin wrapped lines onto their row."""
    rows, cur, buf = {}, None, []
    for line in text.splitlines():
        m = ROW_RE.match(line)
        if m:
            if cur is not None:
                rows[cur] = "".join(buf)
            cur, buf = m.group(1), [m.group(2)]
        else:
            buf.append("\n" + line)
    if cur is not None:
        rows[cur] = "".join(buf)
    return rows


def parse_row(payload):
    """Parse one row payload into a Python value (references still as markers)."""
    payload = payload.strip()
    if payload.startswith("I["):
        # client module import: [id, [deps], exportName]
        try:
            _id, _deps, name = json.loads(payload[1:])
            return {"__module__": name}
        except Exception:
            return {"__module__": payload[1:]}
    if payload.startswith("$S"):
        return f"Symbol({payload[2:]})"
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return payload  # scalar / non-JSON (e.g. null already parses; junk stays)


class Resolver:
    def __init__(self, rows):
        self.raw = rows                      # hexid -> parsed (unresolved) value
        self.memo = {}                       # hexid -> resolved value
        self.stack = set()                   # cycle guard

    def row(self, hid):
        if hid in self.memo:
            return self.memo[hid]
        if hid in self.stack:
            return {"__cycle__": hid}        # break reference cycles
        self.stack.add(hid)
        val = self.resolve(self.raw.get(hid, {"__missing_row__": hid}))
        self.stack.discard(hid)
        self.memo[hid] = val
        return val

    def navigate(self, hid, path):
        """Follow a colon path (keys / list indices) into a resolved row."""
        node = self.row(hid)
        for seg in path.split(":"):
            if isinstance(node, list) and seg.isdigit():
                idx = int(seg)
                node = node[idx] if idx < len(node) else None
            elif isinstance(node, dict):
                node = node.get(seg)
            else:
                return {"__badpath__": f"{hid}:{path}"}
        return node

    def resolve(self, node):
        if isinstance(node, dict):
            return {k: self.resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self.resolve(v) for v in node]
        if isinstance(node, str):
            return self.token(node)
        return node

    def token(self, s):
        if not s.startswith("$"):
            return s
        if s == "$":
            return "$"                       # element marker, keep literal
        if s == "$undefined":
            return None
        if s.startswith("$$"):
            return s[1:]                      # escaped literal '$...'
        body = s[1:]
        # $L<hex>  and  $Q<hex>  and  $<hex>  (optionally with :path)
        m = re.match(r"^([LQ]?)([0-9a-f]+)(?::(.+))?$", body)
        if m:
            _kind, hid, path = m.group(1), m.group(2), m.group(3)
            return self.navigate(hid, path) if path else self.row(hid)
        if body.startswith("n") and body[1:].lstrip("-").isdigit():
            return int(body[1:])              # BigInt
        if body.startswith("S"):
            return f"Symbol({body[1:]})"
        return s                              # unknown -> leave as-is


def main():
    argv = sys.argv
    if len(argv) < 2:
        print(__doc__)
        return 2
    out_path = argv[argv.index("--out") + 1] if "--out" in argv else None

    text = load(argv)
    rows = {hid: parse_row(p) for hid, p in split_rows(text).items()}
    R = Resolver(rows)

    # The root of a Flight document is conventionally row "0".
    root_id = "0" if "0" in rows else sorted(rows)[0]
    resolved = R.row(root_id)

    dump = json.dumps(resolved, indent=2, ensure_ascii=False)
    if out_path:
        open(out_path, "w", encoding="utf-8").write(dump + "\n")
        print(f"resolved root row {root_id} -> {out_path}")
        print(f"rows total: {len(rows)}  resolved(memoized): {len(R.memo)}")
    else:
        print(dump)
    return 0


if __name__ == "__main__":
    sys.exit(main())
