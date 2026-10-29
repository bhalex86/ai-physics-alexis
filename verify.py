"""
verify.py  --  check what the MCP server actually did, independently of Claude.

Every tool call the server makes is signed with a secret key that only this computer
has (~/.cats_mcp_key) and written to audit_log.jsonl. This script recomputes each
signature. If Claude quotes a verification_id that is not in the log, or a log entry
has been edited, it fails.

    python verify.py V-3f9a-007-1c2d3e4f5a6b      check one ID (several allowed)
    python verify.py V-3f9a-007-1c2d3e4f5a6b --expect estimate_deg.low_S=41.2
                                                  also check a number Claude reported
    python verify.py --list                       latest session's calls, in order
    python verify.py --list --session 3f9a        a given session
    python verify.py --files V-...                also re-hash the input files now
                                                  (shows if a file changed since)

In Jupyter use %run verify.py ... with the same arguments.
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def audit_path():
    return Path(os.environ.get("CATS_AUDIT_LOG",
                               Path(os.environ.get("CATS_PROJECT_DIR", HERE)) / "audit_log.jsonl"))


def key():
    p = Path(os.environ.get("CATS_KEY_FILE", Path.home() / ".cats_mcp_key"))
    if not p.exists():
        sys.exit(f"no key file at {p}: the server has not run on this computer yet")
    return bytes.fromhex(p.read_text().strip())


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def load_log():
    p = audit_path()
    if not p.exists():
        sys.exit(f"no audit log at {p}")
    with open(p) as f:
        return [json.loads(line) for line in f if line.strip()]


def check(rec, k):
    """True if the signature matches the logged payload and the ID matches the signature."""
    mac = hmac.new(k, canonical(rec["payload"]).encode(), hashlib.sha256).hexdigest()
    pl = rec["payload"]
    vid = f"V-{pl['session']}-{pl['seq']:03d}-{mac[:12]}"
    return hmac.compare_digest(mac, rec["mac"]) and vid == rec["verification_id"]


def get_path(d, dotted):
    for part in dotted.split("."):
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


def rehash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def show(rec, ok, files=False, expects=(), tol=0.051):
    pl = rec["payload"]
    status = "VALID" if ok else "INVALID (signature does not match: entry altered or forged)"
    print(f"{rec['verification_id']}   {status}")
    print(f"  call #{pl['seq']} in session {pl['session']} at {pl['time']}")
    print(f"  tool:   {pl['tool']}")
    print(f"  inputs: {json.dumps(pl['inputs'])}")
    for fr in pl["files"]:
        line = f"  file:   {fr['path']}  sha256 {fr['sha256'][:16]}...  ({fr['size_bytes']} bytes)"
        if files:
            if not os.path.exists(fr["path"]):
                line += "  -> now MISSING"
            else:
                line += "  -> unchanged" if rehash(fr["path"]) == fr["sha256"] else "  -> CHANGED since"
        print(line)
    if pl["error"]:
        print(f"  error:  {pl['error']}")
    all_ok = ok
    for exp in expects:
        name, _, val = exp.partition("=")
        got = get_path(pl["outputs"] or {}, name)
        try:
            match = got is not None and abs(float(got) - float(val)) <= tol
        except (TypeError, ValueError):
            match = str(got) == val
        all_ok &= match
        print(f"  expect: {name} = {val}   logged: {got}   -> {'MATCH' if match else 'MISMATCH'}")
    return all_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*", help="verification IDs to check")
    ap.add_argument("--list", action="store_true", help="list a session's calls in order")
    ap.add_argument("--session", help="session code (default: the latest)")
    ap.add_argument("--files", action="store_true", help="re-hash input files now")
    ap.add_argument("--expect", action="append", default=[],
                    help="check a logged output, e.g. estimate_deg.low_S=41.2")
    ap.add_argument("--tol", type=float, default=0.051,
                    help="how far a reported number may differ from the logged one "
                         "(default 0.051, i.e. rounding to 0.1; use 0.51 for whole degrees)")
    ap.add_argument("--full", action="store_true", help="print the full logged outputs")
    a = ap.parse_args()

    log, k = load_log(), key()
    if a.list:
        session = a.session or log[-1]["payload"]["session"]
        recs = [r for r in log if r["payload"]["session"] == session]
        print(f"session {session}: {len(recs)} calls, in order\n")
        for r in recs:
            pl = r["payload"]
            print(f"  {pl['seq']:3d}. {pl['tool']:<26} {r['verification_id']}  "
                  f"{'VALID' if check(r, k) else 'INVALID'}"
                  f"{'  (error)' if pl['error'] else ''}")
        return
    if not a.ids:
        ap.error("give one or more verification IDs, or --list")

    by_id = {r["verification_id"]: r for r in log}
    everything_ok = True
    for vid in a.ids:
        rec = by_id.get(vid)
        if rec is None:
            print(f"{vid}   NOT FOUND in {audit_path()}: no tool call produced this ID")
            everything_ok = False
            continue
        everything_ok &= show(rec, check(rec, k), a.files, a.expect, a.tol)
        if a.full:
            print(json.dumps(rec["payload"]["outputs"], indent=2))
        print()
    print("ALL CHECKS PASSED" if everything_ok else "SOME CHECKS FAILED")


if __name__ == "__main__":
    main()
