"""
test_observability_alexis.py  --  HW10: structured JSON logging and reproducibility controls.

    pytest test_observability_alexis.py -v

Offline: the LLM is a fake client (no API key, no network); the MCP server is the real
one, started over stdio on the synthetic sample project.
"""

import asyncio
import json
import os
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "hw8"))

import obs_logging_alexis as obs          # noqa: E402

REQUIRED = {"timestamp", "tool", "input_args", "output_summary", "elapsed_ms", "status"}
ATHDF = HERE / "data" / "athena" / "Turb.out2.00050.athdf"
FAKE_KEY = "sk-ant-api03-THIS-IS-A-FAKE-KEY-0123456789abcdef"


def read_log(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def tool_calls(path):
    return [r for r in read_log(path) if r["event"] == "tool_call"]


@pytest.fixture()
def logfile(tmp_path):
    path = obs.configure("unit", log_file=tmp_path / "unit.jsonl", project_root=tmp_path)
    yield path
    obs.shutdown()


# ------------------------------------------------------------------ the logging module
def test_every_required_field_success_and_failure(logfile):
    @obs.traced("unit.divide")
    def divide(a, b=2):
        return {"ratio": a / b}

    divide(6)
    with pytest.raises(ZeroDivisionError):
        divide(1, b=0)
    ok, bad = tool_calls(logfile)
    for r in (ok, bad):
        assert REQUIRED <= set(r), REQUIRED - set(r)
        assert r["timestamp"].endswith("+00:00") and "T" in r["timestamp"]
        assert isinstance(r["elapsed_ms"], float) and r["elapsed_ms"] >= 0
    assert ok["status"] == "success" and ok["input_args"] == {"a": 6, "b": 2}
    assert ok["output_summary"] == {"ratio": 3.0} and len(ok["output_preview"]) <= 200
    assert bad["status"] == "failure" and bad["error_type"] == "ZeroDivisionError"


def test_api_keys_and_tokens_are_redacted(logfile, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_abcdefghijklmnopqrstuvwxyz0123456789")

    @obs.traced("unit.request")
    def request(url, headers, api_key=None, note=""):
        return {"echo": note, "headers": headers}

    request("https://api.example/x",
            {"Authorization": "Bearer ghp_abcdefghijklmnopqrstuvwxyz0123456789",
             "x-api-key": FAKE_KEY, "Accept": "application/json"},
            api_key=FAKE_KEY, note=f"key is {FAKE_KEY} and token ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    text = Path(logfile).read_text()
    assert FAKE_KEY not in text and "ghp_abcdefghijklmnop" not in text
    r = tool_calls(logfile)[0]
    assert r["input_args"]["api_key"] == obs.REDACTED
    assert r["input_args"]["headers"]["x-api-key"] == obs.REDACTED
    assert r["input_args"]["headers"]["Authorization"] == obs.REDACTED
    assert r["input_args"]["headers"]["Accept"] == "application/json"
    assert r["input_args"]["note"].count(obs.REDACTED) == 2
    assert obs.is_secret_key("GITHUB_TOKEN") and not obs.is_secret_key("max_tokens")


def test_arrays_are_summarised_and_paths_made_relative(logfile, tmp_path):
    big = np.zeros((64, 64, 64))
    obs.call("unit.mean", np.mean, big)
    obs.call("unit.read", lambda path: path.name, tmp_path / "sub" / "file.fits")
    a, b = tool_calls(logfile)
    assert a["input_args"]["a"] == {"type": "ndarray", "shape": [64, 64, 64], "dtype": "float64"}
    assert b["input_args"]["path"] == "sub/file.fits"            # no local absolute path


def test_nested_calls_form_a_tree(logfile):
    @obs.traced("unit.inner")
    def inner(x):
        return x + 1

    @obs.traced("unit.outer")
    def outer(x):
        return inner(inner(x))

    outer(1)
    calls = {r["tool"] + str(r["seq"]): r for r in tool_calls(logfile)}
    outer_rec = [r for r in calls.values() if r["tool"] == "unit.outer"][0]
    inners = [r for r in calls.values() if r["tool"] == "unit.inner"]
    assert outer_rec["parent_seq"] is None and outer_rec["depth"] == 0
    assert [r["parent_seq"] for r in inners] == [outer_rec["seq"]] * 2
    assert all(r["depth"] == 1 for r in inners)


def test_run_start_and_end_records(tmp_path):
    obs.seed_everything(42)
    path = obs.configure("unit", log_file=tmp_path / "u.jsonl", project_root=tmp_path)
    obs.call("unit.noop", lambda: None)
    obs.shutdown()
    recs = read_log(path)
    assert recs[0]["event"] == "run_start" and recs[-1]["event"] == "run_end"
    assert recs[0]["seeds"]["numpy_random_legacy_global"] == 42
    assert "numpy" in recs[0]["versions"] and recs[-1]["n_tool_calls"] == 1


def test_seed_everything_fixes_global_generators():
    import random
    obs.seed_everything(42)
    a = (random.random(), np.random.rand())
    obs.seed_everything(42)
    assert (random.random(), np.random.rand()) == a


def test_nothing_is_logged_until_configured(tmp_path):
    assert not obs.enabled()
    assert obs.call("unit.x", lambda: 3) == 3          # no handler: plain call, no file
    assert not list(tmp_path.iterdir())


# ------------------------------------------------------------------ the pipeline
@pytest.mark.skipif(not ATHDF.exists(), reason="needs data/athena/Turb.out2.00050.athdf")
def test_pipeline_logs_every_tool_and_reruns_identically(tmp_path):
    """Two builds of the Athena++ run: identical cache hash, and a complete JSON trace."""
    import shutil
    import data_pipeline_alexis as pl
    hashes = []
    for name in ("one", "two"):
        proj = tmp_path / name
        shutil.copytree(HERE / "data", proj / "data")
        assert pl.main(["--project", str(proj), "--build", "--source", "athena",
                        "--no-github", "--verify"]) == 0
        obs.shutdown()
        hashes.append(json.loads((proj / "provenance_alexis.json").read_text())
                      ["cache"]["canonical_content_sha256"])
        calls = tool_calls(proj / "logs" / "pipeline_alexis.jsonl")
        assert all(REQUIRED <= set(r) for r in calls)
        tools = {r["tool"] for r in calls}
        assert {"data_pipeline.read_athdf", "data_pipeline.normalise", "data_pipeline.estimate_run",
                "rotate_maps.rotated_maps", "firstmap_b.estimate_from_maps[max]",
                "data_pipeline.write_cache", "data_pipeline.cmd_verify"} <= tools
        assert sum(r["tool"] == "rotate_maps.rotated_maps" for r in calls) == len(pl.ANGLES)
        assert all(r["status"] == "success" for r in calls)
        assert str(proj) not in (proj / "logs" / "pipeline_alexis.jsonl").read_text()
    assert hashes[0] == hashes[1]


# ------------------------------------------------------------------ the evaluation harness
class FakeAnthropic:
    """Stands in for anthropic.Anthropic(): asks for one tool call, then answers."""
    instances = []

    def __init__(self, *a, **k):
        self.calls = []
        self.messages = self
        FakeAnthropic.instances.append(self)

    def create(self, **kw):
        self.calls.append(kw)
        usage = SimpleNamespace(input_tokens=100, output_tokens=20)
        if len(self.calls) == 1:
            block = SimpleNamespace(type="tool_use", name="lookup_result", id="toolu_1",
                                    input={"run_id": "sample_run", "angle": 45})
            return SimpleNamespace(id="msg_1", model=kw["model"], stop_reason="tool_use",
                                   content=[block], usage=usage)
        block = SimpleNamespace(type="text", text='The tilt is 41.8 deg.\n{"value": 41.8, "unit": "deg"}')
        return SimpleNamespace(id="msg_2", model=kw["model"], stop_reason="end_turn",
                               content=[block], usage=usage)


@pytest.fixture(scope="module")
def sample_project(tmp_path_factory):
    import make_sample_data
    d = tmp_path_factory.mktemp("sample") / "sample_project"
    make_sample_data.main(out_dir=d, n=32)
    return d


def test_eval_harness_llm_mode_temperature_zero_and_logged(sample_project, tmp_path, monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    import eval_harness as eh
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("CATS_PROJECT_DIR", str(sample_project))
    monkeypatch.setenv("CATS_KEY_FILE", str(tmp_path / "key"))
    monkeypatch.setenv("CATS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CATS_TOOL_LOG", str(tmp_path / "server.jsonl"))
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps({"claim": "test", "items": [
        {"id": "sample-45", "question": "Tilt of sample_run at 45 deg?", "kind": "numeric",
         "value": 41.8, "unit": "deg", "tolerance": {"abs": 0.5}, "regime": "typical"}]}))
    log = tmp_path / "harness.jsonl"
    asyncio.run(eh.run(Namespace(server=str(HERE / "mcp_server_alexis.py"), truth=str(truth),
                                 mode="llm", only=None, out=None, log_file=str(log))))
    obs.shutdown()

    client = FakeAnthropic.instances[-1]
    assert [c["temperature"] for c in client.calls] == [0.0, 0.0]
    recs = read_log(log)
    calls = [r for r in recs if r["event"] == "tool_call"]
    assert [r["tool"] for r in calls] == ["anthropic.messages.create",
                                          "mcp_client.call_tool:lookup_result",
                                          "anthropic.messages.create"]
    assert all(REQUIRED <= set(r) and r["status"] == "success" for r in calls)
    assert calls[0]["input_args"]["temperature"] == 0.0
    assert calls[0]["output_summary"]["stop_reason"] == "tool_use"
    assert '"value": 41.8' in calls[2]["output_summary"]["content"][0]["text"]
    assert [r["item_id"] for r in recs if r["event"] == "item_scored"] == ["sample-45"]
    assert FAKE_KEY not in log.read_text()

    # the server wrote its own log, linked to this harness run
    srv = read_log(tmp_path / "server.jsonl")
    assert srv[0]["event"] == "run_start" and srv[0]["parent_run_id"] == recs[0]["run_id"]
    srv_calls = [r for r in srv if r["event"] == "tool_call"]
    assert [r["tool"] for r in srv_calls] == ["mcp.lookup_result"]
    assert srv_calls[0]["verification_id"].startswith("V-")


class LoopingAnthropic(FakeAnthropic):
    """A model that never stops asking for tools."""

    def create(self, **kw):
        self.calls.append(kw)
        block = SimpleNamespace(type="tool_use", name="list_runs", id=f"toolu_{len(self.calls)}",
                                input={})
        return SimpleNamespace(id=f"msg_{len(self.calls)}", model=kw["model"],
                               stop_reason="tool_use", content=[block],
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))


def test_eval_harness_reports_a_tool_loop_not_no_answer(sample_project, tmp_path, monkeypatch, capsys):
    """BUG_LOG.md bug 2: hitting the 8-round cap is reported as a loop, not 'no answer'."""
    anthropic = pytest.importorskip("anthropic")
    import eval_harness as eh
    monkeypatch.setattr(anthropic, "Anthropic", LoopingAnthropic)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("CATS_PROJECT_DIR", str(sample_project))
    monkeypatch.setenv("CATS_KEY_FILE", str(tmp_path / "key"))
    monkeypatch.setenv("CATS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CATS_TOOL_LOG", str(tmp_path / "server.jsonl"))
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps({"claim": "test", "items": [
        {"id": "loop", "question": "?", "kind": "numeric", "value": 1.0, "unit": "deg",
         "tolerance": {"abs": 0.5}, "regime": "edge"}]}))
    log, out = tmp_path / "h.jsonl", tmp_path / "rows.json"
    asyncio.run(eh.run(Namespace(server=str(HERE / "mcp_server_alexis.py"), truth=str(truth),
                                 mode="llm", only=None, out=str(out), log_file=str(log))))
    obs.shutdown()
    row = json.loads(out.read_text())[0]
    assert row["failure"].startswith("tool loop") and not row["passed"]
    recs = read_log(log)
    assert sum(r.get("tool") == "anthropic.messages.create" for r in recs) == eh.MAX_TOOL_ROUNDS
    assert any(r["event"] == "tool_loop_cap_reached" for r in recs)
