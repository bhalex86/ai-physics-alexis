"""
trace_alexis.py  --  print the call tree of a run from a JSON tool-call log (HW10).

Rebuilds what happened from the log alone, without running any code: every tool call
in the order it started, nested under the call it happened inside, with its status,
time and a short view of its inputs and output.  Failures and log messages (retries,
missing fields) are shown in place.

    python trace_alexis.py results/key_result_run_alexis.jsonl
    python trace_alexis.py logs/pipeline_alexis.jsonl --run last --args
    python trace_alexis.py logs/mcp_server_alexis.jsonl --failures
"""

import argparse
import json
import sys
from collections import defaultdict


def load(path):
    recs = []
    with open(path) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    print(f"line {n}: not JSON, skipped", file=sys.stderr)
    return recs


def short(obj, n=90):
    s = json.dumps(obj, default=str) if not isinstance(obj, str) else obj
    return s if len(s) <= n else s[:n - 3] + "..."


def print_run(run_id, recs, show_args=False, failures_only=False):
    start = next((r for r in recs if r["event"] == "run_start"), {})
    end = next((r for r in recs if r["event"] == "run_end"), {})
    calls = {r["seq"]: r for r in recs if r["event"] == "tool_call"}
    msgs = defaultdict(list)
    for r in recs:
        if r["event"] not in ("tool_call", "run_start", "run_end"):
            msgs[r.get("parent_seq")].append(r)
    kids = defaultdict(list)
    for seq, r in calls.items():
        kids[r.get("parent_seq")].append(seq)
    git = start.get("git") or {}
    print(f"=== run {run_id}  component={start.get('component')}  "
          f"started {start.get('timestamp')}  git {str(git.get('commit'))[:10]}"
          f"{' (+uncommitted changes)' if git.get('uncommitted_changes') else ''}")
    print(f"    argv: {' '.join(start.get('argv') or [])}")
    if start.get("parent_run_id"):
        print(f"    started by run {start['parent_run_id']}")
    print(f"    seeds: {start.get('seeds')}")

    def show(seq, depth):
        r = calls[seq]
        if failures_only and r["status"] == "success" and not any(
                calls[k]["status"] != "success" for k in kids.get(seq, [])):
            return
        mark = "ok  " if r["status"] == "success" else "FAIL"
        line = f"{'  ' * depth}#{seq:<4} {mark} {r['tool']:<44} {r['elapsed_ms']:>10.1f} ms"
        if r["status"] != "success":
            line += f"  {r.get('error_type')}: {short(r.get('error_message'), 80)}"
        if r.get("verification_id"):
            line += f"  [{r['verification_id']}]"
        print(line)
        if show_args:
            print(f"{'  ' * depth}        in : {short(r.get('input_args'))}")
            if r["status"] == "success":
                print(f"{'  ' * depth}        out: {short(r.get('output_preview'))}")
        for m in msgs.get(seq, []):
            print(f"{'  ' * (depth + 1)}! {m.get('level')} {short(m.get('message'), 110)}")
        for k in sorted(kids.get(seq, [])):
            show(k, depth + 1)

    for m in msgs.get(None, []):
        print(f"  ! {m.get('level')} {short(m.get('message'), 110)}")
    for seq in sorted(kids.get(None, [])):
        show(seq, 0)
    if end:
        print(f"    end: {end.get('n_tool_calls')} tool calls, {end.get('n_failures')} failed")
    else:
        print("    (no run_end record: the run was interrupted or is still going)")
    print()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log")
    ap.add_argument("--run", default="all", help="'all', 'last' or a run_id")
    ap.add_argument("--args", action="store_true", help="show inputs and output previews")
    ap.add_argument("--failures", action="store_true", help="only failed calls and their parents")
    a = ap.parse_args(argv)
    by_run = defaultdict(list)
    for r in load(a.log):
        by_run[r.get("run_id")].append(r)
    runs = list(by_run)
    if a.run == "last":
        runs = runs[-1:]
    elif a.run != "all":
        runs = [r for r in runs if r == a.run]
    if not runs:
        print("no matching run in the log")
        return 1
    for run_id in runs:
        print_run(run_id, by_run[run_id], a.args, a.failures)
    return 0


if __name__ == "__main__":
    sys.exit(main())
