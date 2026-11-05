"""Week 8 Meeting 1 — Evaluation Harness for Your Project's AI Component

Runs every item in a ground-truth file through your Week 7 MCP server and
scores the answers against physical reference values, in physical units.

Goal: by the end of the workshop you should have a results table that puts
      your aggregate metric next to your worst single item, and one
      sentence on whether your project's claim survives.

Steps:
  1. Copy ground_truth_template.json to ground_truth_<lastname>.json and
     write 8-10 items for your own project (at least 2 'edge' items)
  2. Run the harness against your server (LLM + tools, the default)
  3. Complete the TODO in within_tolerance() for your domain
  4. Re-run after your partner adds 2 adversarial items

Usage:
    python eval_harness.py --server mcp_server_<lastname>.py \
        --truth ground_truth_<lastname>.json
    python eval_harness.py --server ... --truth ... --mode tool   # no LLM

Requirements:
    pip install mcp anthropic python-dotenv pint
    ANTHROPIC_API_KEY set in a .env file (only needed for --mode llm)
"""

import argparse
import asyncio
import json
import os
import re
import sys
from collections import defaultdict

import pint
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()
ureg = pint.UnitRegistry()

MODEL = os.environ.get("EVAL_MODEL", "claude-sonnet-5-5")
ANSWER_FORMAT = (
    "Use the available tools to answer. End your reply with one line of JSON "
    'of the form {"value": <number or string>, "unit": "<unit or empty>"} '
    "and nothing after it."
)


# ---------------------------------------------------------------------------
# Getting an answer: either the LLM chooses tool calls, or we call one directly
# ---------------------------------------------------------------------------

def _tool_schemas(tools) -> list[dict]:
    """Convert MCP tool descriptors to the Anthropic tools format."""
    return [
        {"name": t.name, "description": t.description or "",
         # mcp 1.x calls this inputSchema, mcp 2.x input_schema
         "input_schema": getattr(t, "input_schema", None) or t.inputSchema}
        for t in tools
    ]


def _result_text(mcp_result) -> str:
    """Flatten an MCP CallToolResult to a single string."""
    if not mcp_result.content:
        return "null"
    raw = mcp_result.content[0]
    return raw.text if hasattr(raw, "text") else json.dumps(str(raw))


async def answer_with_llm(session, client, schemas, question) -> tuple[str, int]:
    """Agentic loop as in Week 5. Returns (final text, number of tool calls)."""
    messages = [{"role": "user", "content": f"{question}\n\n{ANSWER_FORMAT}"}]
    n_calls = 0
    for _ in range(8):  # hard cap on tool-use rounds per item
        response = client.messages.create(
            model=MODEL, max_tokens=1024, tools=schemas, messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content
                           if hasattr(b, "text")), n_calls
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            n_calls += 1
            mcp_result = await session.call_tool(block.name, block.input)
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": _result_text(mcp_result)})
        messages.append({"role": "user", "content": results})
    return "", n_calls


async def answer_with_tool(session, item) -> str:
    """Call the item's tool directly and pull out one field as the answer."""
    text = _result_text(await session.call_tool(item["tool"],
                                                item.get("arguments", {})))
    value = json.loads(text)[item["field"]] if "field" in item else text
    return json.dumps({"value": value, "unit": item.get("unit", "")})


def parse_answer(text: str) -> dict | None:
    """Return the last {"value": ..., "unit": ...} object in the text."""
    for match in reversed(re.findall(r"\{[^{}]*\}", text)):
        try:
            obj = json.loads(match)
        except json.JSONDecodeError:
            continue
        if "value" in obj:
            return obj
    return None


# ---------------------------------------------------------------------------
# Scoring: errors are reported in the reference unit, never as bare numbers
# ---------------------------------------------------------------------------

def within_tolerance(item, error: float) -> bool:
    """Is an absolute error (in the item's unit) physically acceptable?

    TODO: the three rules below are generic. Replace or extend them with what
    'good enough' means in your domain, e.g. compare to the experimental
    resolution, use a regime-dependent tolerance, or treat a sign error as a
    failure regardless of size. Write one comment line saying why.
    """
    tol = item.get("tolerance", {"rel": 0.05})
    # Domain rule (alexis): the cats-tilt tools report angles to 0.1 deg, and the smallest
    # physical systematic of Chen's estimator is 1-3 deg (changing the p_max rule). So an
    # angle that differs from the answer key by more than 0.5 deg was altered or invented,
    # not rounded, and fails even if the item's own tolerance is looser.
    if item.get("unit") in ("deg", "degree"):
        return error <= min(tol.get("abs", 0.5), 0.5)
    if "abs" in tol:
        return error <= tol["abs"]
    if "rel" in tol:
        return error <= tol["rel"] * abs(item["value"])
    if "sigma" in tol:
        return error <= tol["sigma"] * item["sigma"]
    raise ValueError(f"unknown tolerance {tol}")


def score_item(item, answer: dict | None) -> dict:
    """Compare one parsed answer to its reference value."""
    row = {"id": item["id"], "regime": item.get("regime", "typical"),
           "predicted": None, "error": None, "pull": None,
           "passed": False, "failure": None}
    if answer is None or answer.get("value") in (None, ""):
        row["failure"] = "no answer"
        return row
    row["predicted"] = f"{answer['value']} {answer.get('unit', '')}".strip()

    if item["kind"] == "exact":
        row["passed"] = (str(answer["value"]).strip().lower()
                         == str(item["value"]).strip().lower())
        row["failure"] = None if row["passed"] else "wrong value"
        return row

    try:
        predicted = ureg.Quantity(float(answer["value"]),
                                  answer.get("unit") or item["unit"])
        predicted = predicted.to(item["unit"]).magnitude
    except (pint.errors.DimensionalityError, pint.errors.UndefinedUnitError):
        row["failure"] = "unit error"
        return row
    except (ValueError, TypeError):
        row["failure"] = "not numeric"
        return row

    row["error"] = abs(predicted - item["value"])
    if item.get("sigma"):
        row["pull"] = (predicted - item["value"]) / item["sigma"]
    row["passed"] = within_tolerance(item, row["error"])
    row["failure"] = None if row["passed"] else "outside tolerance"
    return row


def report(truth, items, rows) -> None:
    """Print the per-item table, then aggregates overall and by regime."""
    units = {i["id"]: i.get("unit", "") for i in items}
    print(f"\nClaim: {truth.get('claim', '(none)')}\n")
    print(f"{'id':<24} {'regime':<8} {'predicted':<22} {'|error|':>14} "
          f"{'pull':>7}  result")
    for r in rows:
        err = (f"{r['error']:.4g} {units[r['id']]}"
               if r["error"] is not None else "-")
        pull = f"{r['pull']:+.1f}" if r["pull"] is not None else "-"
        result = "PASS" if r["passed"] else f"FAIL ({r['failure']})"
        if r.get("note"):
            result += f" [{r['note']}]"
        print(f"{r['id']:<24} {r['regime']:<8} {str(r['predicted'])[:22]:<22} "
              f"{err:>14} {pull:>7}  {result}")

    by_regime = defaultdict(list)
    for r in rows:
        by_regime["all"].append(r)
        by_regime[r["regime"]].append(r)
    print()
    for regime, group in by_regime.items():
        passed = sum(r["passed"] for r in group)
        print(f"pass rate [{regime}]: {passed}/{len(group)}")

    # MAE only makes sense within one unit, so group numeric items by unit
    by_unit = defaultdict(list)
    for r in rows:
        if r["error"] is not None:
            by_unit[units[r["id"]]].append(r["error"])
    for unit, errors in by_unit.items():
        print(f"MAE [{unit}]: {sum(errors) / len(errors):.4g} {unit} "
              f"(n={len(errors)}, worst {max(errors):.4g} {unit})")


async def run(args) -> None:
    with open(args.truth) as f:
        truth = json.load(f)
    items = truth["items"]
    if args.only:
        items = [i for i in items if i["id"] in args.only]

    client = None
    if args.mode == "llm":
        import anthropic
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("ANTHROPIC_API_KEY not set. Add it to a .env file, "
                     "or use --mode tool.")
        client = anthropic.Anthropic()

    # alexis: pass the environment through, so CATS_PROJECT_DIR / CATS_ALLOWED_DIRS reach
    # the server (the MCP SDK otherwise starts it with only HOME, PATH and a few others)
    server_params = StdioServerParameters(command=sys.executable,
                                          args=[args.server], env=dict(os.environ))
    rows = []
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            schemas = _tool_schemas((await session.list_tools()).tools)
            for item in items:
                if args.mode == "tool" and "tool" not in item:
                    print(f"[{item['id']}] skipped (no 'tool' for --mode tool)")
                    continue
                try:
                    if args.mode == "llm":
                        text, n_calls = await answer_with_llm(
                            session, client, schemas, item["question"])
                    else:
                        text, n_calls = await answer_with_tool(session, item), 1
                    row = score_item(item, parse_answer(text))
                except Exception as exc:  # a crash is a result, not a stop
                    row = score_item(item, None)
                    row["failure"] = f"tool error: {type(exc).__name__}"
                    n_calls = 0
                if args.mode == "llm" and n_calls == 0 and row["passed"]:
                    row["note"] = "no tool call"
                rows.append(row)
                print(f"[{item['id']}] {'PASS' if row['passed'] else 'FAIL'}")

    if not rows:
        sys.exit("No items were scored.")
    report(truth, items, rows)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(rows, f, indent=2)
        print(f"\nPer-item results written to {args.out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server", required=True,
                        help="path to your MCP server script")
    parser.add_argument("--truth", required=True,
                        help="path to ground_truth_<lastname>.json")
    parser.add_argument("--mode", choices=["llm", "tool"], default="llm",
                        help="llm: LLM chooses tool calls (default); "
                             "tool: call each item's tool directly")
    parser.add_argument("--only", nargs="*", help="run only these item ids")
    parser.add_argument("--out", help="write per-item results to this JSON")
    asyncio.run(run(parser.parse_args()))
