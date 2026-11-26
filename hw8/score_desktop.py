"""
score_desktop.py  --  score Claude Desktop's answers against the HW8 answer key.

Two steps:

  1. python score_desktop.py --make-sheet
       writes desktop_questions_alexis.md   (the questions to paste into Claude Desktop)
              desktop_answers_alexis.csv    (one row per question per trial, to fill in)

  2. after asking every question 3 times and filling in the CSV:
     python score_desktop.py
       scores every trial with the course harness (eval_harness.score_item and my
       within_tolerance rule), checks every verification ID Claude quoted against the
       signed audit log, checks that each number Claude reported actually appears in
       the logged output of a call it cited ("backed"), and reports the protocol's metrics: pass rate (% of trials),
       angle MAE (deg), fabricated answers, pass^3 and the worst item.

In Jupyter: %run score_desktop.py --make-sheet   then   %run score_desktop.py
The answer key (ground_truth_alexis.json) comes from make_ground_truth.py, never from an LLM.
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

TRUTH = Path("ground_truth_alexis.json")
SHEET = Path("desktop_answers_alexis.csv")
QUESTIONS = Path("desktop_questions_alexis.md")
TRIALS = 3
FORMAT = ('End your reply with one line of JSON: {"value": <number or text>, "unit": "<unit or empty>"} '
          "and list the verification_id of every tool result you used.")


def make_sheet(items):
    lines = ["# Questions for Claude Desktop (HW8 evaluation)", "",
             "Ask each question in a **new chat**, 3 times in total (3 separate chats).",
             "Paste the question exactly, then copy Claude's final JSON line and its verification IDs",
             f"into `{SHEET}` (one row per question per trial).", ""]
    for n, it in enumerate(items, 1):
        lines += [f"## {n}. `{it['id']}`", "", f"{it['question']} {FORMAT}", ""]
    QUESTIONS.write_text("\n".join(lines))
    with open(SHEET, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "trial", "value", "unit", "verification_ids", "notes"])
        for it in items:
            for t in range(1, TRIALS + 1):
                w.writerow([it["id"], t, "", "", "", ""])
    print(f"wrote {QUESTIONS} ({len(items)} questions) and {SHEET} ({len(items) * TRIALS} rows to fill in)")


def valid_ids(text):
    """Check each V-... ID against the signed audit log.
    Returns (n_ids, n_valid, payloads of the valid ones)."""
    ids = re.findall(r"V-[0-9a-f]+-\d{3}-[0-9a-f]{12}", text or "")
    if not ids:
        return 0, 0, []
    import verify
    log = {r["verification_id"]: r for r in verify.load_log()}
    k = verify.key()
    good = [log[i]["payload"] for i in ids if i in log and verify.check(log[i], k)]
    return len(ids), len(good), good


def numbers_in(obj):
    """Every number anywhere in a logged tool output."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield float(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from numbers_in(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from numbers_in(v)


def grounded(value, payloads):
    """Is the answer backed by a cited, signed tool result?
    Numbers: the reported value must appear in the logged output of a cited call
    (to the 0.1 the tools report). Text answers: at least one cited call must exist."""
    if not payloads:
        return False
    if isinstance(value, float):
        return any(abs(x - value) <= 0.051 or abs(x - value) <= 1e-3 * abs(x)
                   for pl in payloads for x in numbers_in(pl.get("outputs")))
    return True


def score(items):
    from eval_harness import score_item
    by_id = {it["id"]: it for it in items}
    with open(SHEET, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["value"].strip()]
    if not rows:
        sys.exit(f"{SHEET} has no answers filled in yet")
    unknown = sorted({r["id"] for r in rows} - set(by_id))
    if unknown:
        sys.exit(f"ids not in the answer key: {unknown}")

    results, per_item = [], defaultdict(list)
    for r in rows:
        it = by_id[r["id"]]
        value = r["value"].strip()
        try:
            value = float(value)
        except ValueError:
            pass
        s = score_item(it, {"value": value, "unit": r["unit"].strip()})
        n_ids, n_ok, payloads = valid_ids(r["verification_ids"])
        s.update(trial=r["trial"], category=it.get("category"), reference=it["value"],
                 fabricated=it.get("category") in ("not_available", "planted") and isinstance(value, float),
                 ids=n_ids, ids_valid=n_ok,
                 grounded=None if it.get("category") == "literature" else grounded(value, payloads),
                 tools=",".join(sorted({pl["tool"] for pl in payloads})))
        results.append(s)
        per_item[r["id"]].append(s["passed"])

    print(f"{'id':32s} {'trial':>5} {'reference':>16} {'answer':>16} {'|error|':>8}  {'result':24s} IDs ok  backed  tools")
    for s in results:
        err = f"{s['error']:.3g}" if s["error"] is not None else "-"
        res = "PASS" if s["passed"] else f"FAIL ({s['failure']})"
        if s["fabricated"]:
            res += " FABRICATED"
        print(f"{s['id']:32s} {s['trial']:>5} {str(s['reference']):>16} {str(s['predicted']):>16} "
              f"{err:>8}  {res:24s} {s['ids_valid']}/{s['ids']:<4} {'n/a' if s['grounded'] is None else 'yes' if s['grounded'] else 'NO':6s} {s['tools']}")

    n = len(results)
    passed = sum(s["passed"] for s in results)
    angles = [s["error"] for s in results if by_id[s["id"]].get("unit") == "deg" and s["error"] is not None
              and by_id[s["id"]].get("category") != "literature"]
    fabricated = sum(s["fabricated"] for s in results)
    full = [ok for ok in per_item.values() if len(ok) == TRIALS]
    pass3 = sum(all(ok) for ok in full) / len(full) if full else float("nan")
    ids_total = sum(s["ids"] for s in results)
    ids_bad = ids_total - sum(s["ids_valid"] for s in results)
    print(f"\nCLAUDE pass rate: {100 * passed / n:.1f} % of trials ({passed}/{n})")
    if angles:
        print(f"CLAUDE angle MAE: {sum(angles) / len(angles):.2f} deg (n = {len(angles)}, worst {max(angles):.2f} deg)")
    print(f"CLAUDE fabricated answers: {fabricated}")
    print(f"CLAUDE pass^3: {100 * pass3:.0f} % of questions correct in all {TRIALS} trials ({len(full)} complete)")
    print(f"verification IDs: {ids_total} quoted, {ids_bad} not found in the signed log or invalid")
    checkable = [s for s in results if s["grounded"] is not None]     # the literature item has no tool value
    backed = sum(s["grounded"] for s in checkable)
    print(f"answers backed by a cited, signed tool result: {backed}/{len(checkable)}"
          f"{'' if backed == len(checkable) else '  <- rows marked NO did not come from a cited tool result'}")
    ok = passed / n >= 0.95 and fabricated == 0
    print(f"\nprotocol success (pass rate >= 95 % and 0 fabrications): {'YES' if ok else 'NO'}")
    fails = defaultdict(int)
    for s in results:
        if not s["passed"]:
            fails[s["id"]] += 1
    if fails:
        worst = max(fails, key=fails.get)
        print(f"worst item: {worst} (failed {fails[worst]} of {len(per_item[worst])} trials)")


def main():
    ap = argparse.ArgumentParser(description="score Claude Desktop answers against the HW8 answer key")
    ap.add_argument("--make-sheet", action="store_true", help="write the question list and the blank answer sheet")
    a = ap.parse_args()
    if not TRUTH.exists():
        sys.exit(f"{TRUTH} not found: run make_ground_truth.py (or the baseline notebook) first")
    items = json.loads(TRUTH.read_text())["items"]
    if a.make_sheet:
        if SHEET.exists() and any(r["value"].strip() for r in csv.DictReader(open(SHEET, newline=""))):
            sys.exit(f"{SHEET} already has answers: rename it first so they are not overwritten")
        make_sheet(items)
    else:
        score(items)


if __name__ == "__main__":
    main()
