"""
test_mcp_server_alexis.py  --  tests for mcp_server_alexis.py (PHYS 7440, HW 7).

Every tool endpoint is called through a real MCP client (in memory) with valid and
invalid inputs. Valid replies are checked against the expected output JSON Schema;
invalid ones must come back as is_error = true with a structured JSON error
{"error": {"code", "message", "tool", "verification_id"}} (or, for a missing or
wrongly typed argument, the SDK's schema rejection naming the field).

Runs on a small fake project in a temporary folder (32^3 cubes, fake stored results,
planted failures), with a temporary key and audit log. The CATS network calls are
replaced by a fake HTTP function; set CATS_LIVE=1 to also call the real archive.

    pytest test_mcp_server_alexis.py -v          (Jupyter: !pytest test_mcp_server_alexis.py -v)
"""

import asyncio
import csv
import json
import os
import socket
import sqlite3
import subprocess
import sys
import urllib.error

import jsonschema
import numpy as np
import pytest
from astropy.io import fits

import build_db
import firstmap_b as fm

HERE = os.path.dirname(os.path.abspath(__file__))
ANGLES = [0, 15, 30, 45, 60, 75, 90]

# ============================================================== expected output schemas
NUM = {"type": "number"}
ANGLE = {"type": "number", "minimum": 0, "maximum": 90}
RECEIPT = {"verification_id": {"type": "string", "pattern": r"^V-[0-9a-f]{4}-\d{3,}-[0-9a-f]{12}$"},
           "call_number": {"type": "integer", "minimum": 1}}


def obj(required, props=None, **extra):
    return {"type": "object", "required": ["verification_id", "call_number", *required],
            "properties": {**RECEIPT, **(props or {})}, **extra}


ESTIMATE = {"type": "object", "required": ["all_pixels", "low_S"],
            "properties": {"all_pixels": ANGLE, "low_S": ANGLE}}
UNCERTAINTY = {
    "type": "object", "required": ["low_S", "all_pixels", "comparison_with_chen_2019", "caveats"],
    "properties": {
        "low_S": {"type": "object",
                  "required": ["estimate_deg", "components_deg", "rough_combined_deg",
                               "project_calibration"],
                  "properties": {"components_deg": {
                      "type": "object",
                      "required": ["pmax_rule_sensitivity", "estimator_spread_all_vs_lowS",
                                   "project_calibration_median_abs_err"]}}},
        "comparison_with_chen_2019": {
            "type": "object",
            "required": ["citation", "quoted_accuracy", "table_A1_low_S_error_deg",
                         "nearest_tabulated_tilt_deg", "error_at_that_tilt_deg"],
            "properties": {"nearest_tabulated_tilt_deg": {"enum": [0.0, 30.0, 60.0, 90.0]}}},
        "caveats": {"type": "array", "items": {"type": "string"}, "minItems": 1}}}

FLAT = {"tilt_deg_low_S": ANGLE, "tilt_deg_all_pixels": ANGLE,
        "uncertainty_deg_low_S": {"type": ["number", "null"], "minimum": 0}}

OUTPUT_SCHEMAS = {
    "cats_catalog": obj(["url", "folders", "files", "n_entries", "retrieved_at"], {
        "url": {"type": "string", "pattern": "^https://"},
        "folders": {"type": "array", "items": {"type": "string", "pattern": "/$"}},
        "files": {"type": "array", "items": {"type": "string"}},
        "n_entries": {"type": "integer", "minimum": 0}}),
    "inspect_fits": obj(["file", "hdus", "looks_like"], {
        "file": {"type": "object", "required": ["path", "size_bytes", "sha256"],
                 "properties": {"sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}},
        "hdus": {"type": "array", "minItems": 1,
                 "items": {"type": "object", "required": ["index", "name", "shape", "dtype"]}},
        "looks_like": {"type": "string"}}),
    "estimate_tilt_from_cubes": obj(
        ["input_kind", "view", "pmax_percentile", "estimate_deg", "true_tilt_gamma_B_deg",
         "error_deg", "map_statistics", "field_label_checks", "uncertainty",
         "tilt_deg_low_S", "tilt_deg_all_pixels", "true_tilt_deg", "uncertainty_deg_low_S"], {
            **FLAT, "true_tilt_deg": ANGLE,
            "input_kind": {"const": "simulation cubes"},
            "view": {"type": "object", "required": ["line_of_sight"],
                     "properties": {"line_of_sight": {"enum": ["x", "y", "z", "tilted"]}}},
            "estimate_deg": ESTIMATE, "true_tilt_gamma_B_deg": ANGLE,
            "field_label_checks": {"type": "object",
                                   "required": ["divB_labels_consistent", "anisotropy_consistent"]},
            "uncertainty": UNCERTAINTY}),
    "estimate_tilt_from_stokes": obj(
        ["input_kind", "estimate_deg", "true_tilt_deg", "map_statistics", "uncertainty",
         "n_bad_pixels_dropped", "tilt_deg_low_S", "tilt_deg_all_pixels",
         "uncertainty_deg_low_S"], {
            **FLAT,
            "input_kind": {"const": "Stokes maps"}, "estimate_deg": ESTIMATE,
            "true_tilt_deg": {"type": "null"}, "uncertainty": UNCERTAINTY,
            "n_bad_pixels_dropped": {"type": "integer", "minimum": 0}}),
    "read_source": {"type": "object", "required": ["verification_id", "call_number"],
                    "properties": RECEIPT,
                    "oneOf": [{"required": ["modules", "physics_functions"]},
                              {"required": ["module", "file", "doc", "functions"]},
                              {"required": ["name", "file", "signature", "source", "implements",
                                            "first_line", "last_line"]}]},
    "get_call_log": obj(["session", "calls"], {
        "calls": {"type": "array", "items": {
            "type": "object", "required": ["call_number", "tool", "verification_id", "ok"],
            "properties": {"ok": {"type": "boolean"}}}}}),
    "list_runs": obj(["runs"], {"runs": {"type": "array", "items": {
        "type": "object", "required": ["run_id", "code", "cube_files_present", "stored_results"],
        "properties": {"cube_files_present": {"type": "boolean"}}}}}),
    "run_properties": obj(["source", "run"], {"source": {"type": "string",
                                                         "pattern": r"^db:turbulence:\d+$"}}),
    "load_cube": obj(["run_id", "shape", "mean_field", "field_axis", "dB_over_B0"], {
        "shape": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
        "field_axis": {"enum": ["x", "y", "z"]}, "dB_over_B0": {"type": "number", "minimum": 0}}),
    "make_map": obj(["map_id", "run_id", "angle", "true_tilt_gamma_B", "p_max"], {
        "map_id": {"type": "string", "pattern": "^map:[^:]+:[0-9.]+$"}, "angle": ANGLE,
        "true_tilt_gamma_B": ANGLE, "p_max": {"type": "number", "minimum": 0, "maximum": 1}}),
    "estimate_tilt": obj(["map_id", "gamma_obs", "gamma_obs_lowS", "true_tilt_gamma_B", "err",
                          "err_lowS"], {"gamma_obs": ANGLE, "gamma_obs_lowS": ANGLE}),
    "lookup_result": obj(["source", "run", "rule", "angle", "gamma_obs", "gamma_obs_lowS",
                          "gamma_B_true"], {
        "source": {"type": "string", "pattern": r"^db:results:\d+$"},
        "rule": {"enum": ["max", "p99.5"]}, "angle": ANGLE, "gamma_obs": NUM}),
    "grade_run": obj(["source", "run_id", "pmax_rule", "criteria", "all_pixels", "low_S"], {
        "source": {"type": "array", "items": {"type": "string", "pattern": r"^db:results:\d+$"}},
        "all_pixels": {"type": "object", "required": ["median_abs_err", "slope", "PASS"],
                       "properties": {"PASS": {"type": "boolean"}}}}),
}

ERROR_SCHEMA = {
    "type": "object", "required": ["error"],
    "properties": {"error": {
        "type": "object", "required": ["code", "message", "tool", "verification_id"],
        "properties": {"code": {"enum": ["INVALID_INPUT", "UNKNOWN_RUN", "PATH_NOT_ALLOWED",
                                         "FILE_NOT_FOUND", "NO_DATA", "NOT_FOUND", "TIMEOUT",
                                         "NETWORK_ERROR", "UPSTREAM_ERROR",
                                         "SERVER_NOT_CONFIGURED", "INTERNAL_ERROR",
                                         "UNKNOWN_NAME"]},
                       "message": {"type": "string", "minLength": 1},
                       "verification_id": RECEIPT["verification_id"]}}}}

FAKE_LISTING = """<html><body><table>
<a href="../">../</a><a href="t_500/">t_500/</a><a href="t_900/">t_900/</a>
<a href="dens_t900.fits.gz">dens_t900.fits.gz</a><a href="https://cdn.example/x.css">x</a>
</table></body></html>"""


# ============================================================== fixtures and helpers
@pytest.fixture(scope="module")
def project(tmp_path_factory):
    d = tmp_path_factory.mktemp("proj")
    rho, B = fm.fake_cube(n=32)
    runs = {}
    for rid in ("b1p1_t900", "b1p.01_t900"):
        os.makedirs(d / rid)
        for name, arr in [("dens", rho), ("magx", B["x"]), ("magy", B["y"]), ("magz", B["z"])]:
            fits.PrimaryHDU(arr.astype(np.float32)).writeto(d / rid / f"{name}.fits")
        runs[rid] = {"code": "Cho-ENO", "gravity": False, "snapshot": "t_900", "description": "fake",
                     **{k: f"{rid}/{n}.fits" for k, n in
                        [("rho", "dens"), ("bx", "magx"), ("by", "magy"), ("bz", "magz")]}}
    runs["b1p.32_t900"] = {"code": "Cho-ENO", "gravity": False, "snapshot": "t_900",
                           "description": "x", "planted": "missing run", "rho": "no/d.fits",
                           "bx": "no/x.fits", "by": "no/y.fits", "bz": "no/z.fits"}
    with open(d / "runs.json", "w") as f:
        json.dump({"runs": runs}, f)
    _, Q, U, _, _ = fm.stokes_maps(rho, B, los_axis=0)
    fits.HDUList([fits.PrimaryHDU(rho.sum(axis=0)), fits.ImageHDU(0.15 * Q),
                  fits.ImageHDU(0.15 * U)]).writeto(d / "stokes.fits")
    with open(d / "results_rotated.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run", "angle", "field_axis"] + build_db.RESULT_COLS)
        for run in ("b1p1_t900", "b1p1_t900_pmax100", "b1p.01_t900_pmax100"):
            for a in ANGLES:
                est = 0.8 * a + 10
                w.writerow([run, a, "x", a, a, 0.1, 0.1, 1.0, est, est - 1, a, a, est - a, est - 1 - a])
        for a in ANGLES:
            w.writerow(["b.1p1_t900_pmax100", a, "x", a, 33, 0.07, 0.07, 4.0, 52, 47, 60, 60,
                        52 - a, 47 - a])
    with open(d / "turbulence.csv", "w", newline="") as f:
        csv.writer(f).writerows([["run", "dB_over_B0", "M_s", "M_A"], ["b1p1_t900", 0.58, 0.74, 0.73]])
    cwd = os.getcwd()
    os.chdir(d)
    build_db.build(plant=True, verbose=False)
    os.chdir(cwd)
    os.environ.update(CATS_PROJECT_DIR=str(d), CATS_KEY_FILE=str(d / "key"),
                      CATS_AUDIT_LOG=str(d / "audit.jsonl"), CATS_HTTP_RETRY_DELAY="0")
    return d


def call(name, args=None):
    """Call a tool through an in-memory MCP client. Returns (is_error, payload), where
    payload is the structured result, the parsed JSON error, or the raw error text."""
    from mcp.client.client import Client
    import mcp_server_alexis as srv

    async def go():
        async with Client(srv.mcp) as c:
            r = await c.call_tool(name, args or {})
            if not r.is_error:
                return False, r.structured_content
            text = " ".join(getattr(x, "text", "") for x in r.content)
            i = text.find("{")
            try:
                return True, json.loads(text[i:])
            except (ValueError, IndexError):
                return True, text
    return asyncio.run(go())


def ok(name, args=None):
    err, out = call(name, args)
    assert not err, f"{name} failed: {out}"
    jsonschema.validate(out, OUTPUT_SCHEMAS[name])
    return out


def fails(name, args, code):
    err, out = call(name, args)
    assert err, f"{name} should have failed but returned {out}"
    assert isinstance(out, dict), f"error is not structured JSON: {out}"
    jsonschema.validate(out, ERROR_SCHEMA)
    assert out["error"]["code"] == code, out
    assert out["error"]["tool"] == name
    return out["error"]


def rejected_by_schema(name, args, field):
    """Missing / wrongly typed arguments: rejected by the SDK before the tool runs."""
    err, out = call(name, args)
    assert err and field in str(out), out


def verify(*args):
    r = subprocess.run([sys.executable, os.path.join(HERE, "verify.py"), *args],
                       capture_output=True, text=True, env=os.environ)
    return r.stdout + r.stderr


CUBES = {"density": "b1p1_t900/dens.fits", "bx": "b1p1_t900/magx.fits",
         "by": "b1p1_t900/magy.fits", "bz": "b1p1_t900/magz.fits"}
STOKES = {"stokes_i": "stokes.fits", "stokes_q": "stokes.fits", "stokes_u": "stokes.fits"}


# ============================================================== published input schemas
def test_every_tool_publishes_a_described_input_schema(project):
    from mcp.client.client import Client
    import mcp_server_alexis as srv

    async def go():
        async with Client(srv.mcp) as c:
            return (await c.list_tools()).tools
    tools = asyncio.run(go())
    assert {t.name for t in tools} == set(OUTPUT_SCHEMAS)
    for t in tools:
        assert t.description and t.input_schema["type"] == "object"
        for pname, p in t.input_schema.get("properties", {}).items():
            assert p.get("description"), f"{t.name}.{pname} has no description"
    angle = {t.name: t for t in tools}["lookup_result"].input_schema["properties"]["angle"]
    assert angle["minimum"] == 0 and angle["maximum"] == 90


# ============================================================== cats_catalog (external API)
def test_cats_catalog_valid(project, monkeypatch):
    import mcp_server_alexis as srv
    seen = []
    monkeypatch.setattr(srv, "http_get", lambda url, timeout: seen.append(url) or FAKE_LISTING)
    out = ok("cats_catalog", {"folder": "MHD/256/b1p1/"})
    assert seen == ["https://users.flatironinstitute.org/~bburkhart/data/CATS/MHD/256/b1p1/"]
    assert out["folders"] == ["t_500/", "t_900/"] and out["files"] == ["dens_t900.fits.gz"]


def test_cats_catalog_invalid_folder(project):
    fails("cats_catalog", {"folder": "../../etc/"}, "INVALID_INPUT")
    fails("cats_catalog", {"folder": "MHD/256"}, "INVALID_INPUT")          # no trailing /


def test_cats_catalog_timeout_is_retried_then_reported(project, monkeypatch):
    import mcp_server_alexis as srv
    attempts = []

    def slow(url, timeout):
        attempts.append(1)
        raise socket.timeout("timed out")
    monkeypatch.setattr(srv, "http_get", slow)
    e = fails("cats_catalog", {"folder": "MHD/256/"}, "TIMEOUT")
    assert len(attempts) == 2 and "tried twice" in e["message"]


def test_cats_catalog_404_and_network_errors(project, monkeypatch):
    import mcp_server_alexis as srv

    def missing(url, timeout):
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
    monkeypatch.setattr(srv, "http_get", missing)
    fails("cats_catalog", {"folder": "MHD/999/"}, "NOT_FOUND")

    def offline(url, timeout):
        raise urllib.error.URLError(OSError("nodename nor servname provided"))
    monkeypatch.setattr(srv, "http_get", offline)
    fails("cats_catalog", {"folder": "MHD/256/"}, "NETWORK_ERROR")


@pytest.mark.skipif(not os.environ.get("CATS_LIVE"), reason="set CATS_LIVE=1 to call the real archive")
def test_cats_catalog_live(project):
    out = ok("cats_catalog", {"folder": "MHD/256/b1p1/"})
    assert "t_900/" in out["folders"]


# ============================================================== FITS-file endpoints
def test_inspect_fits_valid(project):
    out = ok("inspect_fits", {"path": "stokes.fits"})
    assert len(out["hdus"]) == 3 and "2-D" in out["looks_like"]


def test_inspect_fits_errors(project):
    fails("inspect_fits", {"path": "../../outside.fits"}, "PATH_NOT_ALLOWED")
    fails("inspect_fits", {"path": "nothere.fits"}, "FILE_NOT_FOUND")
    fails("inspect_fits", {"path": "runs.json"}, "INVALID_INPUT")
    rejected_by_schema("inspect_fits", {}, "path")


def test_estimate_from_cubes_valid_default_view(project):
    out = ok("estimate_tilt_from_cubes", CUBES)
    assert out["view"]["line_of_sight"] == "z"
    assert out["true_tilt_gamma_B_deg"] == pytest.approx(0, abs=0.5)       # fake field is along x


def test_estimate_from_cubes_valid_tilted(project):
    out = ok("estimate_tilt_from_cubes", {**CUBES, "line_of_sight": "tilted", "angle": 45})
    assert out["true_tilt_gamma_B_deg"] == pytest.approx(45, abs=0.01)
    assert "MNRAS 485, 3499" in out["uncertainty"]["comparison_with_chen_2019"]["citation"]


def test_estimate_from_cubes_errors(project):
    fails("estimate_tilt_from_cubes", {**CUBES, "line_of_sight": "tilted", "angle": 120},
          "INVALID_INPUT")
    fails("estimate_tilt_from_cubes", {**CUBES, "line_of_sight": "w"}, "INVALID_INPUT")
    fails("estimate_tilt_from_cubes", {**CUBES, "bz": "stokes.fits"}, "INVALID_INPUT")  # 2-D, not a cube
    fails("estimate_tilt_from_cubes", {**CUBES, "bx": "gone.fits"}, "FILE_NOT_FOUND")
    rejected_by_schema("estimate_tilt_from_cubes", {"density": "b1p1_t900/dens.fits"}, "bx")


def test_estimate_from_stokes_valid(project):
    out = ok("estimate_tilt_from_stokes", STOKES)
    assert out["n_bad_pixels_dropped"] == 0


def test_estimate_from_stokes_errors(project):
    fails("estimate_tilt_from_stokes", {**STOKES, "pmax_percentile": 50}, "INVALID_INPUT")
    fails("estimate_tilt_from_stokes", {**STOKES, "stokes_q": "b1p1_t900/magx.fits"},
          "INVALID_INPUT")                                                     # 3-D, not a map


# ============================================================== project-run endpoints
def test_list_runs_valid(project):
    out = ok("list_runs")
    runs = {r["run_id"]: r for r in out["runs"]}
    assert not runs["b1p.32_t900"]["cube_files_present"]
    assert "planted" not in json.dumps(out)                                     # answer key hidden


def test_list_runs_without_registry(project, monkeypatch):
    monkeypatch.setenv("CATS_PROJECT_DIR", str(project / "b1p1_t900"))         # no runs.json there
    fails("list_runs", {}, "SERVER_NOT_CONFIGURED")


def test_run_properties(project):
    out = ok("run_properties", {"run_id": "b1p1_t900"})
    assert out["dB_over_B0"] == pytest.approx(0.58)
    fails("run_properties", {"run_id": "b1p.01_t900"}, "NO_DATA")
    fails("run_properties", {"run_id": "zzz"}, "UNKNOWN_RUN")


def test_load_cube(project):
    out = ok("load_cube", {"run_id": "b1p1_t900"})
    assert out["field_axis"] == "x" and out["shape"] == [32, 32, 32]
    fails("load_cube", {"run_id": "b1p.32_t900"}, "FILE_NOT_FOUND")             # planted missing run


def test_make_map_and_estimate_tilt(project):
    m = ok("make_map", {"run_id": "b1p1_t900", "angle": 45})
    assert m["true_tilt_gamma_B"] == pytest.approx(45, abs=0.01)
    assert all(not isinstance(v, list) for v in m.values())                    # no arrays returned
    e = ok("estimate_tilt", {"map_id": m["map_id"], "pmax_percentile": 100})
    assert e["map_id"] == m["map_id"]
    fails("make_map", {"run_id": "b1p1_t900", "angle": -5}, "INVALID_INPUT")
    fails("estimate_tilt", {"map_id": "not-a-map"}, "INVALID_INPUT")
    rejected_by_schema("make_map", {"run_id": "b1p1_t900", "angle": "steep"}, "angle")


def test_lookup_result(project):
    out = ok("lookup_result", {"run_id": "b1p1_t900", "angle": 45})
    assert out["gamma_obs"] == pytest.approx(46.0) and "warnings" not in out
    e = fails("lookup_result", {"run_id": "b1p1_t900", "angle": 20}, "NO_DATA")  # no answer stored
    assert "stored angles" in e["message"]
    fails("lookup_result", {"run_id": "b1p1_t900", "angle": 30, "pmax_rule": "median"},
          "INVALID_INPUT")
    rejected_by_schema("lookup_result", {"run_id": "b1p1_t900"}, "angle")


def test_lookup_result_flags_planted_bad_number(project):
    out = ok("lookup_result", {"run_id": "b1p.01_t900", "angle": 45})
    assert out["gamma_obs"] == 412.0
    assert any("outside the physical range" in w for w in out["warnings"])


def test_grade_run(project):
    out = ok("grade_run", {"run_id": "b1p1_t900"})
    assert out["all_pixels"]["PASS"] is True and out["all_pixels"]["slope"] == pytest.approx(0.8)
    bad = ok("grade_run", {"run_id": "b1p.01_t900"})
    assert any("should not be trusted" in w for w in bad["warnings"])
    fails("grade_run", {"run_id": "b1p1_t900", "pmax_rule": "p50"}, "INVALID_INPUT")
    fails("grade_run", {"run_id": "b.1p1_t900", "pmax_rule": "p99.5"}, "UNKNOWN_RUN")


def test_weak_field_regime_is_flagged_without_database(project, tmp_path_factory):
    """Turbulence-dominated cube (dB/B0 >> 1): warn that the estimate is unreliable, and do
    not raise a false 'mislabelled' warning from the anisotropy test (it does not apply)."""
    n, rng = 32, np.random.default_rng(4)
    k = np.fft.fftfreq(n)
    kk = np.sqrt(k[:, None, None]**2 + k[None, :, None]**2 + k[None, None, :]**2)
    kk[0, 0, 0] = 1
    smooth = lambda: np.real(np.fft.ifftn(np.fft.fftn(rng.standard_normal((n, n, n))) * kk**-2.5))
    d1 = lambda a, ax: (np.roll(a, -1, ax) - np.roll(a, 1, ax)) / 2
    A0, A1, A2 = smooth(), smooth(), smooth()            # B = curl A: divergence-free
    Bz, By, Bx = d1(A2, 1) - d1(A1, 2), d1(A0, 2) - d1(A2, 0), d1(A1, 0) - d1(A0, 1)
    s = 0.5 / np.sqrt(np.mean(Bx**2 + By**2 + Bz**2))
    B = {"x": Bx * s + 0.1, "y": By * s, "z": Bz * s}      # dB/B0 = 5: turbulence dominates
    rho = np.exp(0.3 * smooth() / smooth().std())
    d = project / "weak"
    d.mkdir(exist_ok=True)
    for name, arr in [("dens", rho), ("magx", B["x"]), ("magy", B["y"]), ("magz", B["z"])]:
        fits.PrimaryHDU(arr.astype(np.float32)).writeto(d / f"{name}.fits", overwrite=True)
    out = ok("estimate_tilt_from_cubes", {"density": "weak/dens.fits", "bx": "weak/magx.fits",
                                          "by": "weak/magy.fits", "bz": "weak/magz.fits"})
    assert out["field_regime"]["ordered_field_dominates"] is False
    assert out["field_label_checks"]["anisotropy_check_applicable"] is False
    assert any("unreliable" in w for w in out["warnings"])
    assert not any("mislabelled" in w for w in out["warnings"])
    assert "unreliable" in out["uncertainty"]["caveats"][0]
    strong = ok("estimate_tilt_from_cubes", CUBES)                  # fake strong-field cube
    assert strong["field_regime"]["ordered_field_dominates"] is True


# ============================================================== read_source
def test_read_source_index_and_function(project):
    idx = ok("read_source")
    assert "firstmap_b" in idx["modules"]
    assert "Chen eq. 10" in idx["physics_functions"]["firstmap_b.gamma_from_p"]
    f = ok("read_source", {"name": "firstmap_b.gamma_from_p"})
    assert f["source"].startswith("def gamma_from_p") and "eq. 10" in f["implements"]
    assert len(f["file"]["sha256"]) == 64                  # records the exact code version
    m = ok("read_source", {"name": "evaluate"})
    assert "grade" in m["functions"]


def test_read_source_refuses_other_code(project):
    fails("read_source", {"name": "os"}, "UNKNOWN_NAME")
    fails("read_source", {"name": "firstmap_b.nope"}, "UNKNOWN_NAME")
    fails("read_source", {"name": "firstmap_b; import os"}, "INVALID_INPUT")


def test_flat_fields_for_eval_harness(project):
    out = ok("estimate_tilt_from_cubes", CUBES)
    assert out["tilt_deg_low_S"] == out["estimate_deg"]["low_S"]
    assert out["true_tilt_deg"] == out["true_tilt_gamma_B_deg"]
    assert out["uncertainty_deg_low_S"] == out["uncertainty"]["low_S"]["rough_combined_deg"]


# ============================================================== call log and verification
def test_get_call_log_order_and_ids(project):
    a = ok("list_runs")
    b = ok("inspect_fits", {"path": "stokes.fits"})
    log = ok("get_call_log", {"last_n": 2})
    assert [c["tool"] for c in log["calls"]] == ["list_runs", "inspect_fits"]
    assert log["calls"][-1]["verification_id"] == b["verification_id"]
    fails("get_call_log", {"last_n": 0}, "INVALID_INPUT")


def test_verification_ids_check_out_and_forgeries_fail(project):
    out = ok("estimate_tilt_from_cubes", CUBES)
    vid, v = out["verification_id"], out["estimate_deg"]["low_S"]
    rep = verify(vid, "--files", "--expect", f"estimate_deg.low_S={v}")
    assert "VALID" in rep and "unchanged" in rep and "MATCH" in rep and "ALL CHECKS PASSED" in rep
    assert "MISMATCH" in verify(vid, "--expect", f"estimate_deg.low_S={v + 5}")
    assert "NOT FOUND" in verify(vid[:-12] + "0" * 12)


def test_failed_calls_are_signed_too(project):
    e = fails("load_cube", {"run_id": "b1p.32_t900"}, "FILE_NOT_FOUND")
    rep = verify(e["verification_id"])
    assert "VALID" in rep and "FILE_NOT_FOUND" in rep


def test_database_is_read_only(project):
    import mcp_server_alexis as srv
    with pytest.raises(sqlite3.OperationalError):
        with srv.db() as con:
            con.execute("DELETE FROM results")
