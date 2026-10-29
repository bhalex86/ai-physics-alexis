"""
mcp_server_alexis.py  --  MCP server for "Testing a dust-polarization estimator of
magnetic-field inclination in public MHD simulations" (PHYS 7440, B. Alexis).

Ask Claude, for example:
  "Given dens.fits, magx.fits, magy.fits, magz.fits in arepo_B30/grid256, what is the
   estimated tilt, what is its uncertainty compared with Chen et al. (2019), and which
   tools did you use, in order?"

Claude does no physics: it calls these tools and reports what they return.

  cats_catalog              list a folder of the public CATS archive over HTTPS (external API)
  inspect_fits              what is in a FITS file (HDUs, shapes, fingerprint)
  estimate_tilt_from_cubes  simulation cubes (density + Bx, By, Bz) -> tilt + uncertainty
  estimate_tilt_from_stokes observed-style Stokes I, Q, U maps -> tilt + uncertainty
  list_runs, run_properties, load_cube, make_map, estimate_tilt,
  lookup_result, grade_run  the project's approved runs and stored results
  read_source               the source code and docstring of the physics functions, with the
                            Chen et al. (2019) equation each implements
  get_call_log              this session's tool calls, in order, with verification IDs

ERRORS
Arguments of the wrong type, or missing required arguments, are rejected by the MCP SDK
against each tool's inputSchema before the tool runs (is_error = true, naming the field).
Every other failure is returned as is_error = true with a JSON body
    {"error": {"code": "...", "message": "...", "tool": "...", "verification_id": "..."}}
where code is one of ERROR_CODES below. No input makes the server crash.

VERIFICATION
Every tool call, successful or not, is signed with HMAC-SHA256 using a secret key kept
on this computer (default ~/.cats_mcp_key, created on first run) and appended to
audit_log.jsonl. Each reply carries its verification_id (e.g. V-3f9a-007-1c2d3e4f5a6b).
Claude never sees the key, so it cannot invent a valid ID or alter a logged result.
Check any ID, the inputs, the input files' SHA-256 fingerprints and the call order with:
    python verify.py V-3f9a-007-1c2d3e4f5a6b
    python verify.py --list

Data access is read-only: results.db is opened read-only and FITS files are only read,
and only from inside the project folder (plus folders listed in $CATS_ALLOWED_DIRS).
The only file the server writes is the audit log.
"""

import contextvars
import functools
import hashlib
import hmac
import inspect
import json
import math
import os
import re
import secrets
import socket
import ssl
import sqlite3
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Annotated, Any
import urllib.error
import urllib.request

import numpy as np
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import firstmap_b as fm                      # noqa: E402
import rotate_maps as rm                     # noqa: E402
from evaluate import grade                   # noqa: E402
from prepare_arepo import match_components   # noqa: E402

RULES = ("max", "p99.5")
ERROR_CODES = {
    "INVALID_INPUT": "an argument has the right type but an invalid value",
    "UNKNOWN_RUN": "run_id is not in runs.json",
    "PATH_NOT_ALLOWED": "a file path is outside the allowed folders",
    "FILE_NOT_FOUND": "a named file, or a run's cube file, does not exist",
    "NO_DATA": "the request is valid but nothing is stored for it",
    "NOT_FOUND": "the CATS archive has no such folder (HTTP 404)",
    "TIMEOUT": "the CATS archive did not answer in time (after one retry)",
    "NETWORK_ERROR": "the CATS archive could not be reached (DNS, TLS, connection)",
    "UPSTREAM_ERROR": "the CATS archive returned an HTTP error other than 404",
    "SERVER_NOT_CONFIGURED": "runs.json or results.db is missing from the project folder",
    "UNKNOWN_NAME": "read_source was asked for a module or function it does not expose",
    "INTERNAL_ERROR": "an unexpected exception inside a tool (reported, not crashed)",
}
CATS_BASE_URL = "https://users.flatironinstitute.org/~bburkhart/data/CATS/"
ORDERED_FIELD_LIMIT = 1.0    # dB/B0 below this: ordered field dominates (tested 0.1-0.64 pass, 4.4-5.8 fail)
CATS_FOLDER_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*/$")


class ToolFailure(ToolError):
    """An anticipated failure with a machine-readable code (see ERROR_CODES)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _fits_arg(what):
    return Annotated[str, Field(description=f"{what}: path to a FITS file (.fits or .fits.gz), "
                                            "absolute or relative to the project folder; must "
                                            "lie inside an allowed folder.")]


RunId = Annotated[str, Field(description="An approved run ID from list_runs, e.g. 'b1p1_t900'.")]
Angle = Annotated[float, Field(description="Tilt of the mean field out of the plane of the sky, "
                                           "in degrees (0 = in the sky, 90 = along the line of sight).",
                               json_schema_extra={"minimum": 0, "maximum": 90})]
PmaxPct = Annotated[float, Field(description="Percentile of p used as p_max: 100 = map maximum "
                                             "(the pre-registered rule), 99.5 = 99.5th percentile.",
                                 json_schema_extra={"minimum": 90, "maximum": 100})]
PmaxRule = Annotated[str, Field(description="How p_max was chosen for stored results: 'max' = map "
                                            "maximum (pre-registered), 'p99.5' = 99.5th percentile.",
                                json_schema_extra={"enum": list(RULES)})]
LineOfSight = Annotated[str, Field(description="'z', 'y' or 'x': view straight down that box axis "
                                               "as stored. 'tilted': view the mean field tilted by "
                                               "`angle` degrees out of the sky.",
                                   json_schema_extra={"enum": ["z", "y", "x", "tilted"]})]
MapId = Annotated[str, Field(description="A map_id returned by make_map, e.g. 'map:b1p1_t900:45'.",
                             json_schema_extra={"pattern": "^map:[^:]+:[0-9.]+$"})]
LastN = Annotated[int, Field(description="How many of the most recent calls to return.",
                             json_schema_extra={"minimum": 1, "maximum": 500})]
CatsFolder = Annotated[str, Field(description="A folder of the CATS archive, relative to its root "
                                              "and ending in '/', e.g. 'MHD/256/' or "
                                              "'MHD/256/b1p1/t_900/'.",
                                  json_schema_extra={"pattern": CATS_FOLDER_RE.pattern,
                                                     "maxLength": 200})]
FITS_SUFFIXES = (".fits", ".fits.gz", ".fit", ".fit.gz", ".fts")
SESSION = secrets.token_hex(2)
_lock = threading.RLock()
_seq = 0
_cubes = OrderedDict()
_maps = OrderedDict()
_hash_cache = {}
_files_used = contextvars.ContextVar("files_used", default=None)

# Chen et al. (2019) reference values. Errors are low-S estimate minus nominal tilt,
# from their Table A1 (gamma_obs, S<<S>>); quoted ranges from the abstract and Sec. 6.
CHEN = {
    "citation": "Chen C.-Y. et al. 2019, MNRAS 485, 3499",
    "quoted_accuracy": "mean inclination recovered to within 10-25 deg (abstract)",
    "low_S_30_to_60_deg": "about +/-10 deg for true tilts 30-60 deg (Sec. 6, point v)",
    "table_A1_low_S_error_deg": {
        "L1 (least turbulent)": {"0": 19.5, "30": 3.5, "60": -3.6, "90": -17.1},
        "L10": {"0": 29.6, "30": 11.4, "60": -6.5, "90": -22.5},
        "L20 (most turbulent)": {"0": 37.7, "30": 15.4, "60": -9.5, "90": -32.2},
    },
    "note": "Table A1 errors are relative to nominal tilts 0/30/60/90; Chen's true tilts "
            "differ by up to a few degrees (their Table 3).",
}

INSTRUCTIONS = """\
Tools for a physics project that estimates the tilt of a cloud's magnetic field from dust
polarization (Chen et al. 2019) and tests it on public MHD simulations. When answering:
- Use only numbers returned by these tools. Do not compute physics yourself or fill gaps
  from memory. If a tool returns an error or no data, say so plainly.
- Give the verification_id of every tool result you rely on, next to the number.
- For "what is the tilt and its uncertainty", report the estimate, every component of the
  uncertainty block, and the Chen comparison exactly as returned, including its caveats.
- If a result has warnings, report them and do not use a flagged value as if it were valid.
- If a tool returns an error, quote its code and message; do not retry with invented values.
- Use cats_catalog to see what exists in the public CATS archive.
- To explain how a number is computed, call read_source (with no name for an index) and
  quote the relevant function and the Chen equation it implements. Explain from the code;
  take the numbers themselves from the calculation tools, not from your own arithmetic.
- When asked which tools you used, call get_call_log and list the calls in order with
  their verification IDs.
- Angles are in degrees: 0 = field in the plane of the sky, 90 = along the line of sight.
"""

mcp = MCPServer("cats-tilt", instructions=INSTRUCTIONS, version="1.0.0")


# ================================================================ verification
def base_dir():
    return Path(os.environ.get("CATS_PROJECT_DIR", HERE)).resolve()


def audit_path():
    return Path(os.environ.get("CATS_AUDIT_LOG", base_dir() / "audit_log.jsonl"))


def key_path():
    return Path(os.environ.get("CATS_KEY_FILE", Path.home() / ".cats_mcp_key"))


def secret_key():
    p = key_path()
    if not p.exists():
        p.write_text(secrets.token_hex(32))
        os.chmod(p, 0o600)
    return bytes.fromhex(p.read_text().strip())


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sign(payload: dict) -> str:
    return hmac.new(secret_key(), canonical(payload).encode(), hashlib.sha256).hexdigest()


def log_call(tool, inputs, outputs, error):
    global _seq
    with _lock:
        _seq += 1
        payload = json.loads(canonical({
            "session": SESSION, "seq": _seq, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tool": tool, "inputs": inputs, "files": _files_used.get() or [],
            "outputs": outputs, "error": error}))
        mac = sign(payload)
        vid = f"V-{SESSION}-{_seq:03d}-{mac[:12]}"
        with open(audit_path(), "a") as f:
            f.write(canonical({"verification_id": vid, "mac": mac, "payload": payload}) + "\n")
    return vid, _seq


def receipted(fn):
    """Sign and log every call; add verification_id and call_number to the reply."""
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        ba = sig.bind(*args, **kwargs)
        ba.apply_defaults()
        inputs = dict(ba.arguments)
        token = _files_used.set([])
        try:
            try:
                out = fn(*args, **kwargs)
            except ToolError as e:
                code, msg = getattr(e, "code", "INVALID_INPUT"), getattr(e, "message", str(e))
            except Exception as e:                       # unexpected: report, don't crash
                code, msg = "INTERNAL_ERROR", f"{type(e).__name__}: {e}"
            else:
                code = None
            if code is not None:
                vid, _ = log_call(fn.__name__, inputs, None, {"code": code, "message": msg})
                raise ToolError(json.dumps({"error": {"code": code, "message": msg,
                                                      "tool": fn.__name__,
                                                      "verification_id": vid}}))
            vid, n = log_call(fn.__name__, inputs, out, None)
            return {"verification_id": vid, "call_number": n, **out}
        finally:
            _files_used.reset(token)
    return wrapper


def file_record(path: Path):
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime)
    if key not in _hash_cache:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _hash_cache[key] = h.hexdigest()
    rec = {"path": str(path), "size_bytes": st.st_size, "sha256": _hash_cache[key]}
    used = _files_used.get()
    if used is not None and rec not in used:
        used.append(rec)
    return rec


# ================================================================ input checks
def allowed_roots():
    roots = [base_dir()]
    for d in os.environ.get("CATS_ALLOWED_DIRS", "").split(os.pathsep):
        if d.strip():
            roots.append(Path(d).expanduser().resolve())
    return roots


def check_fits_path(path):
    if not isinstance(path, str) or not path.strip():
        raise ToolFailure("INVALID_INPUT", "a FITS file path is required")
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = base_dir() / p
    p = p.resolve()
    if not any(p == r or r in p.parents for r in allowed_roots()):
        raise ToolFailure("PATH_NOT_ALLOWED", f"'{path}' is outside the allowed folders "
                        f"({', '.join(str(r) for r in allowed_roots())})")
    if not p.name.lower().endswith(FITS_SUFFIXES):
        raise ToolFailure("INVALID_INPUT", f"'{p.name}' is not a FITS file (expected {', '.join(FITS_SUFFIXES)})")
    if not p.exists():
        raise ToolFailure("FILE_NOT_FOUND", f"file not found: {p}")
    return p


def check_angle(angle):
    if isinstance(angle, bool) or not isinstance(angle, (int, float)) \
            or not math.isfinite(angle) or not 0 <= angle <= 90:
        raise ToolFailure("INVALID_INPUT", f"angle must be a number from 0 to 90 degrees, got {angle!r}")
    return float(angle)


def check_pct(pct):
    if isinstance(pct, bool) or not isinstance(pct, (int, float)) \
            or not math.isfinite(pct) or not 90 <= pct <= 100:
        raise ToolFailure("INVALID_INPUT", f"pmax_percentile must be from 90 to 100, got {pct!r}")
    return float(pct)


def check_rule(rule):
    if rule not in RULES:
        raise ToolFailure("INVALID_INPUT", f"pmax_rule must be one of {RULES}, got {rule!r}")
    return rule


def registry():
    path = base_dir() / "runs.json"
    if not path.exists():
        raise ToolFailure("SERVER_NOT_CONFIGURED", f"runs.json not found in {base_dir()}")
    with open(path) as f:
        return json.load(f)["runs"]


def check_run(run_id):
    runs = registry()
    if run_id not in runs:
        raise ToolFailure("UNKNOWN_RUN", f"unknown run '{run_id}'. Approved runs: {', '.join(sorted(runs))}")
    return runs[run_id]


def run_files(entry):
    return {k: base_dir() / entry[k] for k in ("rho", "bx", "by", "bz")}


def missing(entry):
    return [str(p.relative_to(base_dir())) for p in run_files(entry).values() if not p.exists()]


def db():
    path = base_dir() / "results.db"
    if not path.exists():
        raise ToolFailure("SERVER_NOT_CONFIGURED", "results.db not found: run build_db.py first")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def rnd(x, n=3):
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) else round(x, n)


def range_warnings(row):
    w = []
    for k, v in row.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if not math.isfinite(v):
            w.append(f"{k} is not a finite number ({v})")
        elif (k.startswith("gamma") or k == "angle") and not 0 <= v <= 90:
            w.append(f"{k} = {v} is outside the physical range 0-90 deg")
        elif k in ("p_max", "p0_derived") and not 0 <= v <= 1:
            w.append(f"{k} = {v} is outside the physical range 0-1")
        elif k in ("err", "err_lowS") and abs(v) > 90:
            w.append(f"{k} = {v} is impossible for angles in 0-90 deg")
    return w


# ================================================================ physics wrappers
def read_fits_array(p: Path, hdu=None):
    from astropy.io import fits
    file_record(p)
    with fits.open(p, memmap=False) as h:
        if hdu is None:
            hdu = next((i for i, x in enumerate(h) if x.data is not None), None)
            if hdu is None:
                raise ToolFailure("INVALID_INPUT", f"{p.name} contains no data")
        if not 0 <= hdu < len(h) or h[hdu].data is None:
            raise ToolFailure("INVALID_INPUT", f"{p.name} has no data in HDU {hdu}")
        return np.asarray(h[hdu].data, dtype=np.float64)


def estimate_core(Q, U, p, pct, mask_border, bad=None):
    """Chen's estimator on maps: p0 from p_max (eq. 9), gamma per pixel (eq. 10),
    most probable value (KDE peak) for all pixels and for S below its median.
    `bad` marks unusable pixels; S is left undefined wherever a neighbour is bad."""
    S = fm.dispersion_S(Q, U)
    if mask_border:
        S[0, :] = S[-1, :] = S[:, 0] = S[:, -1] = np.nan
    if bad is not None and bad.any():
        near_bad = bad.copy()
        for a in (-1, 0, 1):
            for b in (-1, 0, 1):
                near_bad |= np.roll(np.roll(bad, a, 0), b, 1)
        S[near_bad] = np.nan
    ok = np.isfinite(p) & (p > 0)
    pv = p[ok]
    pmax = float(np.percentile(pv, pct))
    p0 = 3 * pmax / (3 + pmax)
    g = np.full(p.shape, np.nan)
    g[ok] = fm.gamma_from_p(pv, p0)
    low = ok & np.isfinite(S) & (S < np.nanmedian(S))
    return {"gamma_obs": fm.kde_peak(g[ok]), "gamma_obs_lowS": fm.kde_peak(g[low]),
            "p_max": pmax, "p0_derived": p0, "median_S_deg": float(np.nanmedian(S)),
            "n_pixels_used": int(ok.sum())}


def calibration(est, col):
    """Errors of this project's stored estimates (rule max) near `est`, split by
    whether the run passed its grade; and the range of estimates from failing runs."""
    try:
        con = db()
    except ToolError as e:
        return {"available": False, "reason": str(e)}
    with con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute("SELECT rowid, * FROM results WHERE rule = 'max'")]
    by_run = {}
    for r in rows:
        if not range_warnings(r):
            by_run.setdefault(r["run"], []).append(r)
    passing, failing = [], []
    for run, rs in by_run.items():
        try:
            g = grade([r["gamma_B_true"] for r in rs], [r[col] for r in rs])
        except ValueError:
            continue
        (passing if g["PASS"] else failing).append(run)
    near = [r for run in passing for r in by_run[run] if abs(r[col] - est) <= 7.5]
    errs = [r[col] - r["gamma_B_true"] for r in near]
    out = {"available": True, "estimator": col, "window_deg": 7.5,
           "passing_runs": sorted(passing), "failing_runs": sorted(failing)}
    if errs:
        out.update(n_maps=len(errs), median_abs_err_deg=rnd(np.median(np.abs(errs)), 2),
                   err_range_deg=[rnd(min(errs), 2), rnd(max(errs), 2)],
                   source_ids=[f"db:results:{r['rowid']}" for r in near][:12])
    else:
        out.update(n_maps=0, note="no stored maps from passing runs have an estimate this close")
    fail_est = [r[col] for run in failing for r in by_run[run]]
    if fail_est:
        lo, hi = min(fail_est), max(fail_est)
        out["failing_runs_estimate_range_deg"] = [rnd(lo, 1), rnd(hi, 1)]
        out["could_be_disordered_field"] = bool(lo - 2 <= est <= hi + 2)
    return out


def chen_near(est):
    nearest = min(("0", "30", "60", "90"), key=lambda a: abs(float(a) - est))
    return {"nearest_tabulated_tilt_deg": float(nearest),
            "error_at_that_tilt_deg": {m: v[nearest] for m, v in
                                       CHEN["table_A1_low_S_error_deg"].items()}}


def uncertainty_block(main, alt, pct_alt):
    """Uncertainty of the low-S estimate (Chen's recommended estimator) and of the
    all-pixel estimate, with each component kept separate."""
    out = {}
    for col, label in (("gamma_obs_lowS", "low_S"), ("gamma_obs", "all_pixels")):
        est = main[col]
        rule = abs(main[col] - alt[col])
        spread = abs(main["gamma_obs"] - main["gamma_obs_lowS"])
        cal = calibration(est, col)
        calerr = cal.get("median_abs_err_deg") if cal.get("available") else None
        comps = [c for c in (rule, spread, calerr) if c is not None]
        out[label] = {
            "estimate_deg": rnd(est, 2),
            "components_deg": {
                "pmax_rule_sensitivity": rnd(rule, 2),
                "pmax_rule_sensitivity_note": f"|estimate with this rule - estimate with the "
                                              f"{pct_alt:g}th percentile|",
                "estimator_spread_all_vs_lowS": rnd(spread, 2),
                "project_calibration_median_abs_err": calerr,
            },
            "rough_combined_deg": rnd(math.sqrt(sum(c * c for c in comps)), 1) if comps else None,
            "rough_combined_note": "quadrature sum of the components above; they are not "
                                   "independent, so treat this as indicative only",
            "project_calibration": cal,
        }
    est = main["gamma_obs_lowS"]
    out["comparison_with_chen_2019"] = {
        **CHEN, **chen_near(est),
        "this_estimate_low_S_deg": rnd(est, 2),
        "this_estimate_rough_uncertainty_deg": out["low_S"]["rough_combined_deg"]}
    caveats = ["Synthetic maps assume optically thin emission and uniform grain alignment.",
               "One map cannot tell an ordered tilted field from a disordered field: in this "
               "project's weak-field runs the estimate stays near 45-58 deg at every tilt."]
    if out["low_S"]["project_calibration"].get("could_be_disordered_field"):
        caveats.insert(0, "This estimate lies inside the range returned by this project's "
                          "failing (disordered-field) runs, so it may not reflect the true tilt.")
    out["caveats"] = caveats
    return out


def flat_fields(out):
    """Top-level copies of the headline numbers, for clients that read one key
    (e.g. the course's eval_harness.py --mode tool, which uses item["field"])."""
    return {"tilt_deg_low_S": out["estimate_deg"]["low_S"],
            "tilt_deg_all_pixels": out["estimate_deg"]["all_pixels"],
            "true_tilt_deg": out.get("true_tilt_gamma_B_deg", out.get("true_tilt_deg")),
            "uncertainty_deg_low_S": out["uncertainty"]["low_S"]["rough_combined_deg"]}


def cube_checks(B):
    perm, best, second, _ = match_components([B["z"], B["y"], B["x"]])
    mean = {k: float(B[k].mean()) for k in "xyz"}
    field = max(mean, key=lambda k: abs(mean[k]))
    rough = {k: sum(float(np.abs(np.roll(B[c] - mean[c], -1, ax) - (B[c] - mean[c])).mean())
                    for c in "xyz") for k, ax in (("z", 0), ("y", 1), ("x", 2))}
    dB = float(np.sqrt(sum(((B[k] - mean[k])**2).mean() for k in "xyz")))
    dB_B0 = dB / float(np.linalg.norm(list(mean.values())))
    # turbulence is only stretched along the field when the ordered field dominates,
    # so the anisotropy test means nothing for dB/B0 >= 1
    applicable = dB_B0 < ORDERED_FIELD_LIMIT
    smooth = min(rough, key=rough.get)
    return {"divB_labels_consistent": perm == (0, 1, 2),
            "divB_best_vs_next_ratio": rnd(second / best, 2),
            "smoothest_axis_of_field_fluctuations": smooth,
            "field_axis": field,
            "anisotropy_check_applicable": applicable,
            "anisotropy_consistent": (smooth == field) if applicable else None,
            "dB_over_B0": rnd(dB_B0, 3)}


def regime(dB_B0):
    """This project's finding: the estimator works when the ordered field dominates
    (dB/B0 = 0.1-0.64 in all four runs tested) and fails when turbulence dominates
    (dB/B0 = 4.4-5.8); values in between were not tested."""
    if dB_B0 < ORDERED_FIELD_LIMIT:
        return {"dB_over_B0": rnd(dB_B0, 3), "ordered_field_dominates": True,
                "note": "ordered field dominates: the regime where the estimator worked in this "
                        "project's tests (dB/B0 = 0.1-0.64)"}
    return {"dB_over_B0": rnd(dB_B0, 3), "ordered_field_dominates": False,
            "note": f"the turbulent field is {dB_B0:.1f} times the mean field: in this project's "
                    "tests (dB/B0 = 4.4-5.8) the estimator failed, staying near 45-58 deg whatever "
                    "the true tilt; treat this estimate as unreliable"}


# ================================================================ tool: external CATS archive
def _ssl_context():
    try:
        import certifi                              # macOS python.org builds need this
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def http_get(url, timeout):
    """One HTTPS GET of a directory listing (module-level so tests can replace it)."""
    req = urllib.request.Request(url, headers={"User-Agent": "mcp-server-alexis/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return r.read(2_000_000).decode("utf-8", "replace")


@mcp.tool()
@receipted
def cats_catalog(folder: CatsFolder = "MHD/256/") -> dict[str, Any]:
    """List one folder of the public CATS simulation archive (Burkhart et al. 2020, ApJ 905,
    14; hosted by the Flatiron Institute) over HTTPS: its sub-folders and files. Use it to
    find which runs, snapshots and files exist before downloading. Nothing is downloaded.
    Examples: 'MHD/256/' (Cho-ENO runs), 'MHD/256/b1p1/t_900/', 'arepo/UniformSnaps_arepo_turb/'."""
    if not isinstance(folder, str) or ".." in folder or len(folder) > 200 \
            or not CATS_FOLDER_RE.match(folder):
        raise ToolFailure("INVALID_INPUT", "folder must be a relative CATS path ending in '/', "
                                           f"e.g. 'MHD/256/b1p1/'; got {folder!r}")
    url = os.environ.get("CATS_BASE_URL", CATS_BASE_URL) + folder
    timeout = float(os.environ.get("CATS_HTTP_TIMEOUT", "20"))
    delay = float(os.environ.get("CATS_HTTP_RETRY_DELAY", "1"))
    failure, html = None, None
    for attempt in (1, 2):                          # one retry for transient failures
        try:
            html = http_get(url, timeout)
            failure = None
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise ToolFailure("NOT_FOUND", f"{url} does not exist in the CATS archive (HTTP 404)")
            failure = ToolFailure("UPSTREAM_ERROR", f"the CATS archive returned HTTP {e.code} for {url}")
        except (TimeoutError, socket.timeout):
            failure = ToolFailure("TIMEOUT", f"no reply from the CATS archive within {timeout:g} s "
                                             f"(tried twice): {url}")
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                failure = ToolFailure("TIMEOUT", f"no reply from the CATS archive within "
                                                 f"{timeout:g} s (tried twice): {url}")
            elif isinstance(reason, ssl.SSLError) or "CERTIFICATE" in str(reason).upper():
                raise ToolFailure("NETWORK_ERROR", f"HTTPS certificate check failed for {url} "
                                                   f"({reason}); install certifi: pip install certifi")
            else:
                failure = ToolFailure("NETWORK_ERROR", f"could not reach {url}: {reason}")
        if attempt == 1:
            time.sleep(delay)
    if failure is not None:
        raise failure
    hrefs = re.findall(r'href="([^"?#]+)"', html)
    entries = sorted({h for h in hrefs if not h.startswith(("/", "http", "../", "./", "mailto:"))})
    out = {"url": url, "folders": [h for h in entries if h.endswith("/")],
           "files": [h for h in entries if not h.endswith("/")],
           "n_entries": len(entries), "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    if not entries:
        out["warnings"] = ["the folder listing contained no entries"]
    return out

# ================================================================ tools: any FITS file
@mcp.tool()
@receipted
def inspect_fits(path: _fits_arg("The file to describe")) -> dict[str, Any]:
    """Describe a FITS file without changing it: each HDU's name, shape, data type and a
    few header keywords, plus the file's SHA-256 fingerprint. Use this first on any file
    the user names, to decide whether it holds a 3-D simulation cube or 2-D Stokes maps."""
    from astropy.io import fits
    p = check_fits_path(path)
    rec = file_record(p)
    hdus = []
    with fits.open(p, memmap=False) as h:
        for i, x in enumerate(h):
            hdr = x.header
            hdus.append({"index": i, "name": x.name,
                         "shape": list(x.data.shape) if x.data is not None else None,
                         "dtype": str(x.data.dtype) if x.data is not None else None,
                         "keywords": {k: str(hdr[k]) for k in
                                      ("BUNIT", "BTYPE", "CTYPE1", "CTYPE2", "OBJECT", "STOKES")
                                      if k in hdr}})
    shapes = [len(x["shape"]) for x in hdus if x["shape"]]
    kind = ("3-D cube (use estimate_tilt_from_cubes with density and Bx, By, Bz files)"
            if 3 in shapes else
            "2-D map (use estimate_tilt_from_stokes with I, Q, U)" if 2 in shapes else "unknown")
    return {"file": rec, "hdus": hdus, "looks_like": kind}


@mcp.tool()
@receipted
def estimate_tilt_from_cubes(density: _fits_arg("3-D gas density cube"),
                             bx: _fits_arg("3-D cube of the x component of B"),
                             by: _fits_arg("3-D cube of the y component of B"),
                             bz: _fits_arg("3-D cube of the z component of B"),
                             line_of_sight: LineOfSight = "z", angle: Angle = 0.0,
                             pmax_percentile: PmaxPct = 100.0) -> dict[str, Any]:
    """Estimate the field tilt from simulation cubes: density and the three magnetic-field
    components, each a 3-D FITS file (FITS axis order z, y, x).
    line_of_sight = "z", "y" or "x" (default "z") views the cube straight down that box
    axis, as it is stored; the true tilt is whatever the data give. line_of_sight =
    "tilted" instead builds a view in which the mean field is tilted by `angle` degrees
    out of the sky (0 = in the sky, 90 = toward the observer); `angle` is ignored otherwise.
    Returns the estimate, the true tilt (known for simulations), checks on the field
    labelling, and an uncertainty block compared with Chen et al. (2019).
    pmax_percentile 100 = map maximum (the pre-registered rule)."""
    if line_of_sight not in ("x", "y", "z", "tilted"):
        raise ToolFailure("INVALID_INPUT", f"line_of_sight must be 'x', 'y', 'z' or 'tilted', got {line_of_sight!r}")
    angle = check_angle(angle)
    pct = check_pct(pmax_percentile)
    paths = {k: check_fits_path(v) for k, v in
             (("density", density), ("bx", bx), ("by", by), ("bz", bz))}
    rho = read_fits_array(paths["density"])
    B = {c: read_fits_array(paths[f"b{c}"]) for c in "xyz"}
    shapes = {rho.shape} | {b.shape for b in B.values()}
    if len(shapes) != 1 or rho.ndim != 3 or len(set(rho.shape)) != 1:
        raise ToolFailure("INVALID_INPUT", f"all four files must be equal n x n x n cubes; got shapes "
                        f"{[rho.shape] + [B[c].shape for c in 'xyz']}")
    if not np.all(rho > 0):
        raise ToolFailure("INVALID_INPUT", "density must be positive everywhere")
    checks = cube_checks(B)
    field_ax, perp_ax, mean = rm.choose_axes(B)
    if line_of_sight == "tilted":
        _, Q, U, p, cos2g = rm.rotated_maps(rho, B, angle, field_ax, perp_ax)
        truth = rm.true_gamma(mean, angle, field_ax, perp_ax)
        view = {"line_of_sight": "tilted", "angle_deg": angle, "tilted_toward_axis": perp_ax}
    else:
        ax = {"z": 0, "y": 1, "x": 2}[line_of_sight]
        _, Q, U, p, cos2g = fm.stokes_maps(rho, B, los_axis=ax)
        truth = fm.true_gamma_B(B, ax)
        view = {"line_of_sight": line_of_sight, "angle_deg": None}
    alt_pct = 99.5 if pct == 100 else 100.0
    main = estimate_core(Q, U, p, pct, mask_border=True)
    alt = estimate_core(Q, U, p, alt_pct, mask_border=True)
    g2d = np.degrees(np.arccos(np.sqrt(np.clip(cos2g, 0, 1))))
    out = {"input_kind": "simulation cubes", "view": view, "pmax_percentile": pct,
           "field_axis": field_ax,
           "estimate_deg": {"all_pixels": rnd(main["gamma_obs"], 2),
                            "low_S": rnd(main["gamma_obs_lowS"], 2)},
           "true_tilt_gamma_B_deg": rnd(truth, 2),
           "true_tilt_gamma_2D_peak_deg": rnd(fm.kde_peak(g2d), 2),
           "error_deg": {"all_pixels": rnd(main["gamma_obs"] - truth, 2),
                         "low_S": rnd(main["gamma_obs_lowS"] - truth, 2)},
           "map_statistics": {"p_max": rnd(main["p_max"], 4), "p0_derived": rnd(main["p0_derived"], 4),
                              "median_S_deg": rnd(main["median_S_deg"], 3),
                              "n_pixels": main["n_pixels_used"]},
           "field_label_checks": checks,
           "field_regime": regime(checks["dB_over_B0"]),
           "uncertainty": uncertainty_block(main, alt, alt_pct)}
    out.update(flat_fields(out))
    w = []
    if not checks["divB_labels_consistent"] or checks["anisotropy_consistent"] is False:
        w.append("field-label checks disagree: Bx/By/Bz may be mislabelled, so the tilt may be wrong")
    if not out["field_regime"]["ordered_field_dominates"]:
        w.append(out["field_regime"]["note"])
        out["uncertainty"]["caveats"].insert(0, out["field_regime"]["note"])
    if w:
        out["warnings"] = w
    return out


@mcp.tool()
@receipted
def estimate_tilt_from_stokes(stokes_i: _fits_arg("2-D Stokes I map"),
                              stokes_q: _fits_arg("2-D Stokes Q map (may be the same file as I)"),
                              stokes_u: _fits_arg("2-D Stokes U map (may be the same file as I)"),
                              pmax_percentile: PmaxPct = 100.0) -> dict[str, Any]:
    """Estimate the field tilt from 2-D Stokes I, Q and U maps (three FITS files, or the
    same file three times if it holds I, Q, U in HDUs 0, 1, 2 or as a 3 x ny x nx array).
    The true tilt is unknown for observations, so the reply gives the estimate and an
    uncertainty block compared with Chen et al. (2019). Noise is not modelled: pixels
    with non-positive I or p are dropped."""
    pct = check_pct(pmax_percentile)
    pi, pq, pu = check_fits_path(stokes_i), check_fits_path(stokes_q), check_fits_path(stokes_u)
    if pi == pq == pu:
        a = read_fits_array(pi)
        if a.ndim == 3 and a.shape[0] >= 3:
            I, Q, U = a[0], a[1], a[2]
        else:
            I, Q, U = (read_fits_array(pi, hdu=h) for h in (0, 1, 2))
    else:
        I, Q, U = read_fits_array(pi), read_fits_array(pq), read_fits_array(pu)
    if not (I.ndim == Q.ndim == U.ndim == 2 and I.shape == Q.shape == U.shape):
        raise ToolFailure("INVALID_INPUT", f"I, Q, U must be 2-D maps of the same shape; got "
                        f"{I.shape}, {Q.shape}, {U.shape}")
    good = np.isfinite(I) & np.isfinite(Q) & np.isfinite(U) & (I > 0)
    if good.sum() < 100:
        raise ToolFailure("INVALID_INPUT", f"only {int(good.sum())} usable pixels (need at least 100)")
    p = np.where(good, np.sqrt(Q**2 + U**2) / np.where(good, I, 1), np.nan)
    if np.nanmax(p) >= 1:
        raise ToolFailure("INVALID_INPUT", "polarization fraction p >= 1 somewhere: check that the files are I, Q, U")
    Qz, Uz = np.where(good, Q, 0.0), np.where(good, U, 0.0)
    alt_pct = 99.5 if pct == 100 else 100.0
    main = estimate_core(Qz, Uz, p, pct, mask_border=True, bad=~good)
    alt = estimate_core(Qz, Uz, p, alt_pct, mask_border=True, bad=~good)
    out = {"input_kind": "Stokes maps", "n_bad_pixels_dropped": int((~good).sum()), "pmax_percentile": pct,
            "estimate_deg": {"all_pixels": rnd(main["gamma_obs"], 2),
                             "low_S": rnd(main["gamma_obs_lowS"], 2)},
            "true_tilt_deg": None, "true_tilt_note": "unknown for observed maps",
            "map_statistics": {"p_max": rnd(main["p_max"], 4), "p0_derived": rnd(main["p0_derived"], 4),
                               "median_S_deg": rnd(main["median_S_deg"], 3),
                               "n_pixels": main["n_pixels_used"]},
            "uncertainty": uncertainty_block(main, alt, alt_pct),
            "observation_caveats": ["No noise or beam model: real data need a signal-to-noise cut "
                                    "and de-biasing before p_max is meaningful.",
                                    "S depends on pixel scale, so median_S_deg is not directly "
                                    "comparable with the simulations' values."]}
    out.update(flat_fields(out))
    return out


@mcp.tool()
@receipted
def get_call_log(last_n: LastN = 50) -> dict[str, Any]:
    """This session's tool calls in the order they were made, with each call's
    verification_id, inputs, and whether it succeeded. Use it to list the tools used."""
    if isinstance(last_n, bool) or not isinstance(last_n, int) or not 1 <= last_n <= 500:
        raise ToolFailure("INVALID_INPUT", "last_n must be an integer from 1 to 500")
    calls = []
    p = audit_path()
    if p.exists():
        with open(p) as f:
            for line in f:
                rec = json.loads(line)
                pl = rec["payload"]
                if pl["session"] == SESSION:
                    calls.append({"call_number": pl["seq"], "tool": pl["tool"],
                                  "verification_id": rec["verification_id"],
                                  "inputs": pl["inputs"], "ok": pl["error"] is None})
    return {"session": SESSION, "calls": calls[-last_n:],
            "how_to_verify": "on the user's computer: python verify.py --list"}


# ================================================================ tool: read the physics code
SourceName = Annotated[str, Field(description="'' for an index of everything readable; a module "
                                              "name (e.g. 'firstmap_b'); or 'module.function' "
                                              "(e.g. 'firstmap_b.gamma_from_p').",
                                  json_schema_extra={"pattern": r"^([A-Za-z_][A-Za-z0-9_]*"
                                                                r"(\.[A-Za-z_][A-Za-z0-9_]*)?)?$",
                                                     "maxLength": 100})]
SOURCE_MODULES = ("firstmap_b", "rotate_maps", "evaluate", "prepare_arepo", "mcp_server_alexis")
PHYSICS_MAP = {
    "firstmap_b.stokes_maps": "Chen et al. 2019 eqs. 1-3 (Stokes q, u and the N2 correction) "
                              "and eq. 2 (polarization fraction p)",
    "firstmap_b.gamma_from_p": "Chen eq. 10 (tilt gamma from p and p0)",
    "firstmap_b.dispersion_S": "Chen eqs. 5-6 (polarization-angle dispersion S, 8 neighbours)",
    "firstmap_b.kde_peak": "most probable value of a distribution via Gaussian KDE (Chen Sec. 2.2)",
    "firstmap_b.estimate_from_maps": "Chen eq. 9 (p0 from p_max), eq. 10, and the low-S selection",
    "firstmap_b.true_gamma_B": "Chen eq. 15 (tilt of the box-averaged field)",
    "rotate_maps.rotated_maps": "eqs. 1-3 integrated along a tilted line of sight",
    "rotate_maps.true_gamma": "Chen eq. 15 for a tilted view",
    "mcp_server_alexis.estimate_core": "Chen eq. 9, eq. 10 and the low-S selection, as run by "
                                       "the estimate_tilt* tools",
    "mcp_server_alexis.uncertainty_block": "this project's uncertainty components and the "
                                           "comparison with Chen Table A1",
    "evaluate.grade": "this project's success criteria (median |error| and slope over 30-60 deg)",
    "prepare_arepo.match_components": "div B = 0 check of the field-component labels",
}
MAX_SOURCE_CHARS = 20000


def _module(name):
    if name not in SOURCE_MODULES:
        raise ToolFailure("UNKNOWN_NAME", f"module '{name}' is not readable; readable modules: "
                                          f"{', '.join(SOURCE_MODULES)}")
    return sys.modules[__name__] if name == "mcp_server_alexis" else sys.modules[name]


def _functions(mod):
    return {n: f for n, f in vars(mod).items()
            if inspect.isfunction(f) and getattr(f, "__module__", None) == mod.__name__}


def _first_line(doc):
    return (inspect.cleandoc(doc).splitlines() or [""])[0] if doc else ""


@mcp.tool()
@receipted
def read_source(name: SourceName = "") -> dict[str, Any]:
    """Read the code behind the tools. With no name: an index of the readable modules and
    their functions, marking which implement which Chen et al. (2019) equation. With a
    module name: its docstring and function list. With 'module.function': that function's
    source code, docstring, file, line numbers and the file's SHA-256 (so the exact code
    version is recorded). Use it to explain how a result is computed."""
    if not isinstance(name, str) or len(name) > 100 or (
            name and not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)?", name)):
        raise ToolFailure("INVALID_INPUT", f"name must be '', 'module' or 'module.function'; got {name!r}")
    if not name:
        return {"modules": {m: {"doc": _first_line(_module(m).__doc__),
                                "functions": sorted(_functions(_module(m)))}
                            for m in SOURCE_MODULES},
                "physics_functions": PHYSICS_MAP}
    mod_name, _, fn_name = name.partition(".")
    mod = _module(mod_name)
    rec = file_record(Path(mod.__file__))
    if not fn_name:
        return {"module": mod_name, "file": rec, "doc": inspect.cleandoc(mod.__doc__ or ""),
                "functions": {n: _first_line(f.__doc__) for n, f in sorted(_functions(mod).items())}}
    fns = _functions(mod)
    if fn_name not in fns:
        raise ToolFailure("UNKNOWN_NAME", f"'{fn_name}' is not a function in {mod_name}; "
                                          f"available: {', '.join(sorted(fns))}")
    fn = inspect.unwrap(fns[fn_name])
    lines, start = inspect.getsourcelines(fn)
    src = "".join(lines)
    out = {"name": name, "file": rec, "first_line": start, "last_line": start + len(lines) - 1,
           "signature": f"{fn_name}{inspect.signature(fn)}",
           "doc": inspect.cleandoc(fn.__doc__ or ""),
           "implements": PHYSICS_MAP.get(name), "source": src[:MAX_SOURCE_CHARS],
           "truncated": len(src) > MAX_SOURCE_CHARS}
    return out


# ================================================================ tools: project runs
@mcp.tool()
@receipted
def list_runs() -> dict[str, Any]:
    """List the project's approved simulation runs, whether their cube files are present,
    and which stored results (p_max rules and angles) exist for each."""
    runs = registry()
    stored = {}
    try:
        with db() as con:
            for run, rule, angles in con.execute(
                    "SELECT run, rule, GROUP_CONCAT(angle) FROM results GROUP BY run, rule"):
                stored.setdefault(run, {})[rule] = sorted(float(a) for a in angles.split(","))
    except ToolError:
        pass
    return {"runs": [{"run_id": rid, "code": e.get("code"), "gravity": e.get("gravity"),
                      "snapshot": e.get("snapshot"), "description": e.get("description"),
                      "cube_files_present": not missing(e),
                      "stored_results": stored.get(rid, {})}
                     for rid, e in sorted(runs.items())]}


@mcp.tool()
@receipted
def run_properties(run_id: RunId) -> dict[str, Any]:
    """Stored turbulence measurements for one approved run: mean field and its axis,
    dB/B0, density spread, sonic and Alfven Mach numbers (null if not measured)."""
    check_run(run_id)
    with db() as con:
        con.row_factory = sqlite3.Row
        cols = [r[1] for r in con.execute("PRAGMA table_info(turbulence)")]
        if len(cols) <= 1:
            raise ToolFailure("NO_DATA", "no turbulence measurements are stored")
        row = con.execute("SELECT rowid, * FROM turbulence WHERE run = ?", (run_id,)).fetchone()
    if row is None:
        raise ToolFailure("NO_DATA", f"no turbulence measurements stored for '{run_id}'")
    d = dict(row)
    rid = d.pop("rowid")
    return {"source": f"db:turbulence:{rid}",
            **{k: (rnd(v, 4) if isinstance(v, float) else v) for k, v in d.items()}}


def project_cube(run_id):
    e = check_run(run_id)
    miss = missing(e)
    if miss:
        raise ToolFailure("FILE_NOT_FOUND", f"files for run '{run_id}' are missing: {', '.join(miss)}")
    f = run_files(e)
    for p in f.values():
        file_record(p)
    with _lock:
        if run_id in _cubes:
            _cubes.move_to_end(run_id)
            return _cubes[run_id]
    rho, B = fm.load_cubes(*(str(f[k]) for k in ("rho", "bx", "by", "bz")))
    field_ax, perp_ax, mean = rm.choose_axes(B)
    c = {"rho": rho, "B": B, "field_ax": field_ax, "perp_ax": perp_ax, "mean": mean}
    with _lock:
        _cubes[run_id] = c
        while len(_cubes) > 2:
            _cubes.popitem(last=False)
    return c


def project_map(run_id, angle):
    mid = f"map:{run_id}:{angle:g}"
    with _lock:
        if mid in _maps:
            _maps.move_to_end(mid)
            for p in run_files(check_run(run_id)).values():
                file_record(p)
            return mid, _maps[mid]
    c = project_cube(run_id)
    _, Q, U, p, cos2g = rm.rotated_maps(c["rho"], c["B"], angle, c["field_ax"], c["perp_ax"])
    m = {"Q": Q, "U": U, "p": p, "cos2g": cos2g,
         "truth": rm.true_gamma(c["mean"], angle, c["field_ax"], c["perp_ax"])}
    with _lock:
        _maps[mid] = m
        while len(_maps) > 8:
            _maps.popitem(last=False)
    return mid, m


@mcp.tool()
@receipted
def load_cube(run_id: RunId) -> dict[str, Any]:
    """Load an approved run's cubes and summarize them (shape, mean field and its axis,
    dB/B0, density statistics). Returns numbers only, never arrays."""
    c = project_cube(run_id)
    B, rho, mean = c["B"], c["rho"], c["mean"]
    dB = float(np.sqrt(sum(((B[k] - B[k].mean())**2).mean() for k in "xyz")))
    return {"run_id": run_id, "shape": list(rho.shape),
            "mean_field": {k: rnd(v, 6) for k, v in zip("xyz", mean)},
            "field_axis": c["field_ax"], "dB_over_B0": rnd(dB / np.linalg.norm(mean), 4),
            "density_max_over_mean": rnd(rho.max() / rho.mean(), 2),
            "density_sigma_over_mean": rnd(rho.std() / rho.mean(), 4)}


@mcp.tool()
@receipted
def make_map(run_id: RunId, angle: Angle) -> dict[str, Any]:
    """Make one synthetic polarization map of an approved run viewed at `angle` degrees.
    Returns a map_id and summary statistics, not the map."""
    angle = check_angle(angle)
    mid, m = project_map(run_id, angle)
    p = m["p"]
    return {"map_id": mid, "run_id": run_id, "angle": angle,
            "true_tilt_gamma_B": rnd(m["truth"], 2), "n_pixels": int(p.size),
            "p_median": rnd(np.median(p), 4), "p_99.5th_percentile": rnd(np.percentile(p, 99.5), 4),
            "p_max": rnd(p.max(), 4)}


@mcp.tool()
@receipted
def estimate_tilt(map_id: MapId, pmax_percentile: PmaxPct = 100.0) -> dict[str, Any]:
    """Run Chen et al.'s estimator on a map from make_map. pmax_percentile 100 = map
    maximum (the pre-registered rule). Returns estimates, true tilt and errors."""
    pct = check_pct(pmax_percentile)
    parts = map_id.split(":") if isinstance(map_id, str) else []
    if len(parts) != 3 or parts[0] != "map":
        raise ToolFailure("INVALID_INPUT", f"'{map_id}' is not a map_id from make_map (expected map:<run>:<angle>)")
    try:
        angle = check_angle(float(parts[2]))
    except ValueError:
        raise ToolFailure("INVALID_INPUT", f"'{map_id}' has no valid angle")
    mid, m = project_map(parts[1], angle)
    r = estimate_core(m["Q"], m["U"], m["p"], pct, mask_border=True)
    out = {"map_id": mid, "pmax_percentile": pct, "true_tilt_gamma_B": rnd(m["truth"], 2),
           **{k: rnd(v, 3) for k, v in r.items() if k != "n_pixels_used"},
           "err": rnd(r["gamma_obs"] - m["truth"], 3),
           "err_lowS": rnd(r["gamma_obs_lowS"] - m["truth"], 3)}
    w = range_warnings(out)
    if w:
        out["warnings"] = w
    return out


@mcp.tool()
@receipted
def lookup_result(run_id: RunId, angle: Angle, pmax_rule: PmaxRule = "max") -> dict[str, Any]:
    """Fetch one stored result for an approved run at `angle` degrees, with p_max chosen
    by `pmax_rule` ("max" = pre-registered, "p99.5" = 99.5th percentile)."""
    check_run(run_id)
    angle = check_angle(angle)
    check_rule(pmax_rule)
    with db() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT rowid, * FROM results WHERE run = ? AND rule = ? "
                          "AND ABS(angle - ?) < 1e-6", (run_id, pmax_rule, angle)).fetchone()
        have = [a for (a,) in con.execute("SELECT angle FROM results WHERE run = ? AND rule = ? "
                                          "ORDER BY angle", (run_id, pmax_rule))]
    if row is None:
        raise ToolFailure("NO_DATA", f"no stored result for run '{run_id}', angle {angle:g}, rule "
                        f"'{pmax_rule}'; " + (f"stored angles: {have}" if have else
                                              "no results are stored for this run and rule"))
    d = dict(row)
    out = {"source": f"db:results:{d.pop('rowid')}",
           **{k: (rnd(v, 3) if isinstance(v, float) else v) for k, v in d.items()}}
    w = range_warnings(out)
    if w:
        out["warnings"] = w
    return out


@mcp.tool()
@receipted
def grade_run(run_id: RunId, pmax_rule: PmaxRule = "max") -> dict[str, Any]:
    """Grade an approved run's stored results over true tilts 30-60 deg: median signed
    and absolute error, slope, and PASS (median |error| < 15 deg AND slope > 0.5)."""
    check_run(run_id)
    check_rule(pmax_rule)
    with db() as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute("SELECT rowid, * FROM results WHERE run = ? AND "
                                             "rule = ? ORDER BY angle", (run_id, pmax_rule))]
    if not rows:
        raise ToolFailure("NO_DATA", f"no stored results for run '{run_id}' with rule '{pmax_rule}'")
    ids = [f"db:results:{r['rowid']}" for r in rows]
    warns = [f"{i} (angle {r['angle']:g}): {w}" for r, i in zip(rows, ids) for w in range_warnings(r)]
    out = {"source": ids, "run_id": run_id, "pmax_rule": pmax_rule,
           "criteria": "median |err| < 15 deg AND slope > 0.5, true tilts 30-60 deg"}
    truth = [r["gamma_B_true"] for r in rows]
    for col, label in (("gamma_obs", "all_pixels"), ("gamma_obs_lowS", "low_S")):
        try:
            g = grade(truth, [r[col] for r in rows])
            out[label] = {k: (rnd(v, 3) if isinstance(v, float) else v) for k, v in g.items()}
        except ValueError as e:
            out[label] = {"error": str(e)}
    if warns:
        out["warnings"] = warns + ["the grade includes the flagged values and should not be "
                                   "trusted until they are fixed"]
    return out


if __name__ == "__main__":
    mcp.run()
