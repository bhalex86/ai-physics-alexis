"""
data_pipeline_alexis.py  --  data ingestion layer for the dust-polarization tilt project.

PHYS 7440, HW9.  Bianca Alexis.

What it does
------------
1. FETCH   Two external sources, one common schema:
             * CATS Cho-ENO simulation cubes (density + Bx, By, Bz as FITS) over plain
               HTTPS from the Flatiron CATS folder;
             * an Athena++ MHD run of my own (HDF5 ``.athdf``), built at a pinned
               GitHub commit, whose commit record is fetched from the GitHub API.
2. BUILD   Normalise each run (rho / <rho>, B / |<B>|), measure dB/B0, M_s, M_A, run the
           Chen et al. (2019) estimator at several viewing angles using the existing
           ``firstmap_b.py`` and ``rotate_maps.py`` code, and write everything to a
           SQLite cache plus ``.npy`` map files.
3. VERIFY  Recompute a hash of the cache and compare it with the one recorded in
           ``provenance_alexis.json``.

Failure handling
----------------
(a) Timeouts / rate limits: ``tenacity`` retries (4 attempts = 3 retries, exponential
    backoff 1, 2, 4 s plus random jitter, ``Retry-After`` honoured) on requests.Timeout, ConnectionError,
    HTTP 429, HTTP 5xx and a GitHub 403 with ``X-RateLimit-Remaining: 0``.  A 404 is never
    retried: the dataset is recorded as ``data_status = "missing"``.
(b) Missing or null fields: each is logged with the field name, file and source URL and
    replaced by a documented sentinel (see ``SENTINEL_FLOAT``, ``SENTINEL_STR``,
    ``SENTINEL_COUNT``).  The estimator refuses any row whose *required* inputs are
    missing: it is stored as ``DATA_UNAVAILABLE`` and never becomes a number.

Reproducibility
---------------
No timestamps are stored in the cache.  Access times live only in the provenance file.
Rows are sorted and floats rounded, so the same raw files give the same cache content.
``--verify`` compares a *canonical content hash* (rows serialised as sorted JSON), not the
raw bytes of the ``.db`` file, because SQLite writes its own library version into the file
header and that differs between machines.

Usage
-----
    python data_pipeline_alexis.py --fetch --build --verify              # both sources
    python data_pipeline_alexis.py --fetch --build --source cats          # CATS site only
    python data_pipeline_alexis.py --build --source athena                # my Athena++ run only
    python data_pipeline_alexis.py --fetch --runs b.1p.01_t750 --athena-file data/athena/Turb.out2.00050.athdf
    python data_pipeline_alexis.py --help

Units decision (one place: ``alfven_speed``)
--------------------------------------------
Athena++ reports B in code units with mu = 1 (no 4 pi), so v_A = B / sqrt(rho).  The
CATS cubes have no velocity field and unknown field units, so M_s and M_A are not computed
for them (sentinel NaN, logged as optional missing fields).  The tilt depends only on the
direction of B, so normalising by |<B>| makes it unit-free for every source.
"""

import argparse
import datetime as _dt
import hashlib
import json
import logging
import math
import os
import platform
import re
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import requests
from tenacity import (RetryCallState, retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential, wait_random)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import firstmap_b as fm      # noqa: E402  Stokes maps, Chen eqs. 1-3, 9, 10
import obs_logging_alexis as obs   # noqa: E402  HW10: JSON log of every tool call
import rotate_maps as rm     # noqa: E402  tilted lines of sight

# HW10 reproducibility: seed the global generators first thing (random.seed(42) and
# numpy.random.seed(42)).  The estimator's own randomness (the KDE subsample in
# firstmap_b.kde_peak) uses a local generator with seed 0; see REPRODUCIBILITY.md.
SEEDS = obs.seed_everything(42)

try:                          # optional: read a GitHub token from .env
    from dotenv import load_dotenv
    load_dotenv()
except Exception:             # python-dotenv is optional
    pass

log = logging.getLogger("pipeline")

# ------------------------------------------------------------------ constants
PIPELINE_VERSION = "1.1"   # 1.1 (HW10): JSON tool-call log; source-URL fix (BUG_LOG.md)
CATS_BASE_URL = os.environ.get("CATS_BASE_URL",
                               "https://users.flatironinstitute.org/~bburkhart/data/CATS/")
GITHUB_API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")
ATHENA_REPO = "PrincetonUniversity/athena"
ATHENA_SHA = "1395e2f543f4368d448cb1a2cf92ba1f910e3416"

SENTINEL_FLOAT = float("nan")     # stored in SQLite as NULL (SQLite has no NaN)
SENTINEL_STR = "UNKNOWN"
SENTINEL_COUNT = -1
FLOAT_DECIMALS = 6                # fixed precision for every stored float

# viewing tilts, degrees.  HW10: 5 deg steps (was 0/15/.../90), so the 30-60 deg grade uses
# 7 tilts per snapshot instead of 3 (PHYSICS_REVIEW_alexis.md, finding 8)
ANGLES = list(range(0, 91, 5))
PMAX_RULES = {"max": 100.0, "p99.5": 99.5}    # p_max = map maximum / 99.5th percentile
STAT_TIMEOUT = (float(os.environ.get("PIPELINE_CONNECT_TIMEOUT", "10")),
                float(os.environ.get("PIPELINE_READ_TIMEOUT", "60")))   # seconds
BACKOFF_INITIAL = float(os.environ.get("PIPELINE_BACKOFF_INITIAL", "1.0"))
BACKOFF_MAX = float(os.environ.get("PIPELINE_BACKOFF_MAX", "30.0"))
BACKOFF_JITTER = float(os.environ.get("PIPELINE_BACKOFF_JITTER", "1.0"))
RETRY_AFTER_CAP = 120.0                       # never sleep longer than this on a header hint
MAX_ATTEMPTS = 4                              # 1 try + 3 retries

# CATS runs registered by the project: legacy name -> (run folder, snapshot).
# HW10: three snapshots per run (finding 8: one snapshot gives no estimate of the run-to-run
# scatter).  The first-listed snapshot of each run is its PRIMARY one, used up to HW9 and by
# the HW8 answer key.
CATS_SNAPSHOTS = {
    "b1p1": (900, 850, 800),
    "b1p.01": (900, 850, 800),
    "b.1p1": (900, 850, 800),
    "b.1p.01": (750, 700, 650),
}
CATS_RUNS = {f"{run}_t{snap}": (run, snap) for run, snaps in CATS_SNAPSHOTS.items()
             for snap in snaps}
PRIMARY_CATS_RUNS = sorted(f"{run}_t{snaps[0]}" for run, snaps in CATS_SNAPSHOTS.items())
DEFAULT_CATS_RUNS = ["b.1p.01_t750"]
COMPONENTS = {"rho": "dens", "bx": "magx", "by": "magy", "bz": "magz"}

CITATIONS = [
    "Burkhart, B. et al. 2020, ApJ 905, 14 (CATS), doi:10.3847/1538-4357/abc484",
    "Cho, J. & Lazarian, A. 2003, MNRAS 345, 325",
    "Burkhart, B. et al. 2009, ApJ 693, 250",
    "Portillo, S. K. N. et al. 2018, ApJ 862, 119",
    "Bialy, S. & Burkhart, B. 2020, ApJ 894, L2",
    "Stone, J. M. et al. 2020, ApJS 249, 4 (Athena++)",
    "Chen, C.-Y. et al. 2019, MNRAS 485, 3499, doi:10.1093/mnras/stz618",
]

ALFVEN_CONVENTION = {
    "athenapp": "v_A = B / sqrt(rho): code units with mu = 1 (no 4 pi), Stone et al. 2020",
    "cats_cho_eno": "not computed: no velocity cubes and field units not specified",
}

SCHEMA = """
CREATE TABLE datasets (
    run_id TEXT PRIMARY KEY,           -- canonical id, e.g. cho:b.1p.01:t750
    legacy_name TEXT NOT NULL,         -- name used by results.db and the MCP server
    source TEXT NOT NULL,              -- cats_cho_eno | athenapp_own_run
    code TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    source_url TEXT NOT NULL,
    data_status TEXT NOT NULL,         -- ok | missing | unreachable | unusable
    grid_n INTEGER NOT NULL,           -- -1 = unknown
    sha256_rho TEXT, sha256_bx TEXT, sha256_by TEXT, sha256_bz TEXT,
    rho_mean REAL, B0 REAL,            -- raw <rho> and |<B>| before normalisation
    ux REAL, uy REAL, uz REAL,         -- unit vector of <B>
    field_axis TEXT NOT NULL,
    dB_over_B0 REAL, M_s REAL, M_A REAL, gamma_B_0deg REAL,
    bunit TEXT NOT NULL,
    alfven_convention TEXT NOT NULL,
    missing_fields TEXT NOT NULL       -- comma-separated names, '' if none
);
CREATE TABLE estimates (
    run_id TEXT NOT NULL, angle INTEGER NOT NULL, rule TEXT NOT NULL,
    status TEXT NOT NULL,              -- ok | DATA_UNAVAILABLE
    field_axis TEXT NOT NULL,
    gamma_B_true REAL, gamma_2D_peak REAL, p_max REAL, p0_derived REAL,
    median_S_deg REAL, gamma_obs REAL, gamma_obs_lowS REAL,
    gamma_obs_truep0 REAL, gamma_obs_truep0_lowS REAL, err REAL, err_lowS REAL,
    PRIMARY KEY (run_id, angle, rule)
);
CREATE TABLE maps (                    -- informational; excluded from the canonical hash
    run_id TEXT NOT NULL, angle INTEGER NOT NULL,
    input_signature TEXT NOT NULL, map_content_sha256 TEXT NOT NULL,
    PRIMARY KEY (run_id, angle)
);
"""
RESULT_COLS = ["gamma_B_true", "gamma_2D_peak", "p_max", "p0_derived", "median_S_deg",
               "gamma_obs", "gamma_obs_lowS", "gamma_obs_truep0", "gamma_obs_truep0_lowS",
               "err", "err_lowS"]
CANONICAL_TABLES = {"datasets": "run_id", "estimates": "run_id, angle, rule"}


# ================================================================== small helpers
def sha256_file(path):
    """Return the hex SHA-256 of a file, read in 1 MiB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data):
    """Return the hex SHA-256 of a bytes object."""
    return hashlib.sha256(data).hexdigest()


def mtime_utc(path):
    """File modification time as ISO-8601 UTC (provenance only; approximate acquisition time
    for files that were not downloaded by the pipeline)."""
    return _dt.datetime.fromtimestamp(Path(path).stat().st_mtime, _dt.timezone.utc
                                      ).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now():
    """Current time as an ISO-8601 UTC string (provenance only, never the cache)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rnd(x, n=FLOAT_DECIMALS):
    """Round a float to fixed precision; NaN/inf/None become None (SQL NULL = sentinel)."""
    if x is None:
        return None
    x = float(x)
    return None if not math.isfinite(x) else round(x, n)


def is_sentinel(x):
    """True if a stored value is one of the documented sentinels (None/NaN/'UNKNOWN'/-1)."""
    return x is None or x == SENTINEL_STR or (isinstance(x, float) and math.isnan(x))


# ================================================================== failure mode (b)
MISSING = []     # every missing-field event of this process: dicts with field/file/url
PROJECT_ROOT = HERE   # set by main(); used to keep logged paths project-relative


def note_missing(field, file, url, sentinel, required=False):
    """Log a missing or null field with its file and source URL and record it.

    The caller substitutes ``sentinel`` for the value.  Nothing is raised, so the
    pipeline keeps running; the estimator later refuses rows whose *required* inputs
    are sentinels.
    """
    ev = {"field": field, "file": relpath(file, PROJECT_ROOT) if Path(str(file)).is_absolute()
          else str(file), "source_url": url,
          "sentinel": "NaN" if isinstance(sentinel, float) and math.isnan(sentinel) else sentinel,
          "required": bool(required)}
    MISSING.append(ev)
    log.warning("MISSING FIELD %s in %s (source %s); substituting sentinel %r",
                field, file, url, ev["sentinel"])
    return sentinel


def header_get(header, key, default, file, url):
    """Read a FITS header keyword, or log it as missing and return ``default``."""
    if header is not None and key in header and str(header[key]).strip() not in ("", "None"):
        return header[key]
    return note_missing(key, file, url, default)


# ================================================================== failure mode (a)
class RetryableHTTPError(Exception):
    """A response worth retrying: HTTP 429, 5xx or a rate-limited GitHub 403."""

    def __init__(self, status, url, retry_after=None):
        """Store the status code, URL and any server-suggested delay in seconds."""
        super().__init__(f"HTTP {status} for {url}")
        self.status, self.url, self.retry_after = status, url, retry_after


class NotFoundError(Exception):
    """HTTP 404: the resource does not exist.  Never retried."""


class HTTPFailure(Exception):
    """Any other non-retryable HTTP error (e.g. 401, 400)."""


RETRY_EVENTS = []   # retry log, copied into provenance


def _wait_with_retry_after(state: RetryCallState):
    """Exponential backoff (1, 2, 4 ... s) plus random jitter, lengthened to honour a
    Retry-After header when the server sends one (capped at ``RETRY_AFTER_CAP``)."""
    base = (wait_exponential(multiplier=BACKOFF_INITIAL, max=BACKOFF_MAX)
            + wait_random(0, BACKOFF_JITTER))(state)
    exc = state.outcome.exception() if state.outcome else None
    hint = getattr(exc, "retry_after", None)
    return max(base, min(float(hint), RETRY_AFTER_CAP)) if hint else base


def _before_sleep(state: RetryCallState):
    """Log (and record in ``RETRY_EVENTS``) each retry that is actually about to happen."""
    exc = state.outcome.exception() if state.outcome else None
    wait = state.next_action.sleep if state.next_action else 0.0
    RETRY_EVENTS.append({"attempt": state.attempt_number, "error": str(exc),
                         "wait_s": round(float(wait), 3)})
    log.warning("retry %d/%d in %.2f s after: %s", state.attempt_number, MAX_ATTEMPTS - 1,
                wait, exc, extra={"retry_attempt": state.attempt_number,
                                  "retry_wait_s": round(float(wait), 3),
                                  "retry_error": str(exc)})


def _parse_retry_after(resp):
    """Seconds to wait according to Retry-After / X-RateLimit-Reset, or None."""
    ra = resp.headers.get("Retry-After")
    if ra:
        try:
            return float(ra)
        except ValueError:
            return None
    reset = resp.headers.get("X-RateLimit-Reset")
    if reset and resp.headers.get("X-RateLimit-Remaining") == "0":
        try:
            return max(0.0, float(reset) - time.time())
        except ValueError:
            return None
    return None


RETRY_ON = (requests.Timeout, requests.ConnectionError, requests.exceptions.ChunkedEncodingError,
            RetryableHTTPError)


def _retrying(fn):
    """Wrap ``fn`` in the project's retry policy: 4 attempts, exponential backoff plus jitter,
    ``Retry-After`` honoured.  Retries timeouts, connection errors, cut-off transfers
    (ChunkedEncodingError), HTTP 429, 5xx and rate-limited GitHub 403s."""
    return retry(retry=retry_if_exception_type(RETRY_ON), wait=_wait_with_retry_after,
                 stop=stop_after_attempt(MAX_ATTEMPTS), before_sleep=_before_sleep,
                 reraise=True)(fn)


@obs.traced("data_pipeline.http_get_attempt")
def _get_once(url, headers=None, stream=False):
    """One GET attempt, classifying the response.  Returns the open ``requests.Response``.

    404 raises ``NotFoundError`` (never retried); 429, 5xx and a 403 with
    ``X-RateLimit-Remaining: 0`` raise ``RetryableHTTPError``; other 4xx raise ``HTTPFailure``.
    Transport compression is switched off so downloaded bytes are the file's bytes.
    """
    hdrs = {"Accept-Encoding": "identity", **(headers or {})}
    resp = requests.get(url, headers=hdrs, stream=stream, timeout=STAT_TIMEOUT)
    code = resp.status_code
    if code == 404:
        resp.close()
        raise NotFoundError(url)
    if code == 429 or code >= 500:
        ra = _parse_retry_after(resp)
        resp.close()
        raise RetryableHTTPError(code, url, ra)
    if code == 403 and (resp.headers.get("X-RateLimit-Remaining") == "0"
                        or resp.headers.get("Retry-After")):       # primary / secondary limit
        ra = _parse_retry_after(resp)
        resp.close()
        raise RetryableHTTPError(code, url, ra)
    if code >= 400:
        resp.close()
        raise HTTPFailure(f"HTTP {code} for {url}")
    return resp


http_get = _retrying(_get_once)
http_get.__doc__ = ("GET ``url`` with exponential backoff (see ``_retrying``) and return the "
                    "response.  404 raises NotFoundError at once; other 4xx raise HTTPFailure.")


def relpath(path, project):
    """Path relative to the project folder when inside it (keeps provenance portable).

    The unresolved path is tried first, so a run folder that is a symlink to data stored
    elsewhere (e.g. ``b1p1 -> /data/cats/b1p1``) is still recorded as ``b1p1/...``.
    """
    for a, b in ((Path(path).absolute(), Path(project).absolute()),
                 (Path(path).resolve(), Path(project).resolve())):
        try:
            return str(a.relative_to(b))
        except ValueError:
            continue
    return str(path)


@obs.traced("data_pipeline.download_attempt")
def _download_once(url, dest, project):
    """One attempt to stream ``url`` into ``dest`` (via a .part file).

    Raises ``requests.ConnectionError`` if fewer bytes arrive than Content-Length announced,
    so a cut-off transfer is retried instead of being saved as a corrupt file.
    """
    resp = _get_once(url, stream=True)
    part = dest.with_name(dest.name + ".part")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        n = 0
        with open(part, "wb") as f:
            for chunk in resp.iter_content(1 << 20):
                f.write(chunk)
                h.update(chunk)
                n += len(chunk)
        want = resp.headers.get("Content-Length")
        if want is not None and want.isdigit() and int(want) != n:
            raise requests.ConnectionError(f"incomplete download: got {n} of {want} bytes: {url}")
        os.replace(part, dest)
        hdrs = {k: resp.headers.get(k) for k in ("Content-Length", "Last-Modified", "ETag",
                                                 "Content-Type", "Server", "Date")
                if resp.headers.get(k) is not None}
    finally:
        resp.close()
        if part.exists():
            part.unlink()
    return {"url": url, "path": relpath(dest, project), "sha256": h.hexdigest(),
            "bytes": dest.stat().st_size, "http_headers": hdrs, "access_time_utc": utc_now(),
            "acquired_by": "downloaded by this pipeline"}


download = _retrying(_download_once)
download.__doc__ = ("Download ``url`` to ``dest`` with the retry policy (the whole transfer is "
                    "retried, including cut-off streams).  Returns a provenance record: URL, "
                    "SHA-256, size, selected response headers, access time.")


# ================================================================== provenance state
class Provenance:
    """Reads and updates ``provenance_alexis.json``.

    Access times, headers, hashes and transformations are recorded here (never in the
    cache).  ``--fetch`` fills the ``files`` part; ``--build`` fills the ``cache`` part.
    """

    def __init__(self, path):
        """Load the existing file if there is one, else start an empty record."""
        self.path = Path(path)
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        else:
            self.data = {}
        self.data.setdefault("files", {})
        self.data.setdefault("datasets", {})
        self.data.setdefault("api_calls", {})

    def record_file(self, key, rec):
        """Store a file record under ``key`` and flag whether its SHA-256 changed."""
        old = self.data["files"].get(key)
        if old and old.get("sha256") and rec.get("sha256"):
            rec["sha256_matches_previous_record"] = (old["sha256"] == rec["sha256"])
        self.data["files"][key] = rec

    def save(self):
        """Write the provenance file with stable key order."""
        self.data["pipeline"] = {
            "name": "data_pipeline_alexis.py", "version": PIPELINE_VERSION,
            "script_sha256": sha256_file(Path(__file__)),
            "python": platform.python_version(), "platform": platform.platform(),
            "packages": _package_versions(),
        }
        self.data["constants"] = {
            "intrinsic_polarization_p0": fm.P0_TRUE,
            "viewing_angles_deg": ANGLES,
            "pmax_rules": PMAX_RULES,
            "float_decimals_in_cache": FLOAT_DECIMALS,
            "sentinels": {"float": "NaN (stored as SQL NULL)", "string": SENTINEL_STR,
                          "count": SENTINEL_COUNT},
            "alfven_speed_convention": ALFVEN_CONVENTION,
        }
        self.data["citations"] = CITATIONS
        self.data["retry_policy"] = {"library": "tenacity", "max_attempts": MAX_ATTEMPTS,
                                     "backoff": "wait_exponential + wait_random jitter",
                                     "multiplier_s": BACKOFF_INITIAL, "max_s": BACKOFF_MAX,
                                     "jitter_s": BACKOFF_JITTER,
                                     "retry_on": ["requests.Timeout", "ConnectionError",
                                                  "cut-off transfer (ChunkedEncodingError / short body)",
                                                  "HTTP 429", "HTTP 5xx",
                                                  "HTTP 403 with X-RateLimit-Remaining: 0 or Retry-After"],
                                     "not_retried": ["HTTP 404 (recorded as data_status=missing)"]}
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n")


def _package_versions():
    """Versions of the packages that affect the numbers."""
    from importlib.metadata import PackageNotFoundError, version
    out = {}
    for name in ("numpy", "scipy", "astropy", "h5py", "requests", "tenacity", "pandas"):
        try:
            out[name] = version(name)          # tenacity has no __version__ attribute
        except PackageNotFoundError:
            out[name] = SENTINEL_STR
    return out


# ================================================================== source 1: CATS
def cats_urls(run_folder, snap, comp):
    """Candidate URLs for one CATS file: gzip first, then uncompressed (some densities)."""
    stem = f"{COMPONENTS[comp]}_t{snap}"
    base = f"{CATS_BASE_URL.rstrip('/')}/MHD/256/{run_folder}/t_{snap}/{stem}"
    return [(base + ".fits.gz", f"{stem}.fits.gz"), (base + ".fits", f"{stem}.fits")]


def cats_url_of(local, run_folder, snap, comp):
    """The archive URL of a local CATS file: the candidate whose file name matches it.

    BUG_LOG.md, bug 1: the URL used to be ``cats_urls(...)[0][0]`` (always the ``.fits.gz``
    candidate), which is a 404 for runs whose density is stored uncompressed
    (``dens_t750.fits`` of b.1p.01).  ``local=None`` (file not on disk) keeps the first
    candidate, the URL that was tried first.
    """
    cands = cats_urls(run_folder, snap, comp)
    if local is not None:
        for url, fname in cands:
            if Path(local).name == fname:
                return url
    return cands[0][0]


def find_local(project, run_folder, snap, comp):
    """Return an existing local CATS file for this component, or None."""
    stem = f"{COMPONENTS[comp]}_t{snap}"
    for ext in (".fits.gz", ".fits"):
        p = project / run_folder / (stem + ext)
        if p.exists():
            return p
    return None


@obs.traced("data_pipeline.fetch_cats_run", skip_args=("prov",))
def fetch_cats_run(legacy, project, prov, force=False):
    """Make sure the four cubes of one CATS run are on disk; record provenance.

    Existing local files are used as they are (and recorded as ``pre-existing local
    file``: no access time is invented for them).  Returns a status string:
    ``ok``, ``missing`` (HTTP 404), or ``unreachable`` (retries exhausted).
    """
    run_folder, snap = CATS_RUNS[legacy]
    status = "ok"
    for comp in COMPONENTS:
        local = None if force else find_local(project, run_folder, snap, comp)
        if local is not None:
            prov.record_file(f"{legacy}:{comp}", {
                "url": cats_url_of(local, run_folder, snap, comp), "path": relpath(local, project),
                "sha256": sha256_file(local), "bytes": local.stat().st_size,
                "access_time_utc": None, "local_file_modified_utc": mtime_utc(local),
                "acquired_by": "pre-existing local file (not downloaded by this pipeline run; "
                "original download time not recorded; modification time is only an "
                "approximation)"})
            continue
        got = False
        for url, fname in cats_urls(run_folder, snap, comp):
            try:
                rec = download(url, project / run_folder / fname, project)
                prov.record_file(f"{legacy}:{comp}", rec)
                got = True
                break
            except NotFoundError:
                log.warning("404 (not retried): %s", url)
                continue
            except RETRY_ON + (HTTPFailure,) as e:
                log.error("giving up on %s after %d attempts: %s", url, MAX_ATTEMPTS, e)
                status = "unreachable"
                break
        if not got and status != "unreachable":
            status = "missing"
        if status != "ok":
            prov.record_file(f"{legacy}:{comp}", {"url": cats_urls(run_folder, snap, comp)[0][0],
                                                 "sha256": None, "status": status,
                                                 "access_time_utc": utc_now()})
            break
    return status


@obs.traced("data_pipeline.load_cats_cubes", skip_args=("prov",))
def load_cats_cubes(legacy, project, prov):
    """Read the four FITS cubes of a CATS run (float64) and return (rho, B, info).

    ``info`` holds the FITS header of the density cube, its text (for the notebook) and
    the optional header fields, with sentinels substituted and logged when absent.
    """
    from astropy.io import fits
    run_folder, snap = CATS_RUNS[legacy]
    paths = {c: find_local(project, run_folder, snap, c) for c in COMPONENTS}
    arrays = {}
    for c, p in paths.items():
        arrays[c] = np.asarray(fits.getdata(p), dtype=np.float64)
    shapes = {a.shape for a in arrays.values()}
    if len(shapes) != 1:
        raise ValueError(f"cube shapes differ for {legacy}: {shapes}")
    hdr = fits.getheader(paths["rho"])
    url0 = cats_url_of(paths["rho"], run_folder, snap, "rho")
    info = {"header_text": hdr.tostring(sep="\n"),
            "bunit": str(header_get(hdr, "BUNIT", SENTINEL_STR, paths["rho"], url0)),
            "shas": {c: sha256_file(p) for c, p in paths.items()}}
    rho = arrays["rho"]
    B = {"x": arrays["bx"], "y": arrays["by"], "z": arrays["bz"]}
    return rho, B, info


# ================================================================== source 2: Athena++
@obs.traced("data_pipeline.github_commit", skip_args=("prov",))
def github_commit(sha, raw_dir, prov):
    """Fetch the GitHub API JSON for the pinned Athena++ commit (with retries).

    Uses ``GITHUB_TOKEN`` from the environment / .env if set (60 requests/hour without
    one).  On failure returns a record with ``status`` set; the pipeline carries on.
    """
    url = f"{GITHUB_API_URL}/repos/{ATHENA_REPO}/commits/{sha}"
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    rec = {"url": url, "status": "ok"}
    try:
        resp = http_get(url, headers=headers)
        body = resp.content
        resp.close()
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"github_commit_{sha[:7]}.json").write_bytes(body)
        try:
            j = json.loads(body)
            assert isinstance(j, dict)
        except (ValueError, AssertionError):
            (raw_dir / f"github_commit_{sha[:7]}.json").unlink(missing_ok=True)
            note_missing("commit JSON", "github_commit", url, SENTINEL_STR)
            rec.update(status="unparseable_response", commit_sha=SENTINEL_STR)
            prov.data["api_calls"]["github_commit"] = rec
            return rec
        rec.update(sha256=sha256_bytes(body), access_time_utc=utc_now(),
                   commit_sha=j.get("sha", SENTINEL_STR),
                   commit_message=(j.get("commit", {}).get("message") or SENTINEL_STR).split("\n")[0],
                   commit_date=(j.get("commit", {}).get("committer", {}) or {}).get("date")
                   or note_missing("commit.committer.date", "github_commit", url, SENTINEL_STR))
        if j.get("sha") != sha:
            rec["status"] = "sha_mismatch"
    except NotFoundError:
        rec.update(status="missing", commit_sha=SENTINEL_STR)
    except RETRY_ON + (HTTPFailure,) as e:
        log.error("GitHub API unavailable after %d attempts: %s", MAX_ATTEMPTS, e)
        rec.update(status="unreachable", commit_sha=SENTINEL_STR)
    prov.data["api_calls"]["github_commit"] = rec
    return rec


@obs.traced("data_pipeline.read_athdf")
def read_athdf(path):
    """Read an Athena++ ``.athdf`` file into full-box arrays using h5py only.

    Returns (rho, B, vel, meta).  ``B`` and ``vel`` are dicts with keys x, y, z (or
    ``None`` when the file has no such variables: e.g. the code was built without ``-b``).
    Arrays have shape (nz, ny, nx), the same axis order as ``firstmap_b.py``.  Only a
    uniform (single-level) mesh is supported.
    """
    import h5py
    with h5py.File(path, "r") as f:
        names = [n.decode() if isinstance(n, bytes) else n for n in f.attrs["VariableNames"]]
        dsets = [n.decode() if isinstance(n, bytes) else n for n in f.attrs["DatasetNames"]]
        nvars = [int(n) for n in f.attrs["NumVariables"]]
        levels = f["Levels"][:]
        if len(set(levels.tolist())) != 1:
            raise ValueError("refined mesh (several levels) is not supported")
        root = [int(v) for v in f.attrs["RootGridSize"]]            # nx1, nx2, nx3
        mbs = [int(v) for v in f.attrs["MeshBlockSize"]]
        locs = f["LogicalLocations"][:]
        var_loc, k = {}, 0                                          # name -> (dataset, index)
        for ds, nv in zip(dsets, nvars):
            for i in range(nv):
                var_loc[names[k]] = (ds, i)
                k += 1

        def assemble(name):
            """Return the full (nz, ny, nx) cube of one variable, or None if absent."""
            if name not in var_loc or var_loc[name][0] not in f:
                return None
            ds, idx = var_loc[name]
            data = f[ds][idx]                                       # (nmb, nk, nj, ni)
            out = np.empty((root[2], root[1], root[0]), dtype=np.float64)
            for m in range(data.shape[0]):
                i0, j0, k0 = (int(locs[m][0]) * mbs[0], int(locs[m][1]) * mbs[1],
                              int(locs[m][2]) * mbs[2])
                out[k0:k0 + mbs[2], j0:j0 + mbs[1], i0:i0 + mbs[0]] = data[m]
            return out

        rho = assemble("rho")
        B = {a: assemble(n) for a, n in zip("xyz", ("Bcc1", "Bcc2", "Bcc3"))}
        vel = {a: assemble(n) for a, n in zip("xyz", ("vel1", "vel2", "vel3"))}
        meta = {"time": float(f.attrs["Time"]), "root_grid": root, "variable_names": names}
    if any(v is None for v in B.values()):
        B = None
    if any(v is None for v in vel.values()):
        vel = None
    return rho, B, vel, meta


def alfven_speed(code, B0, rho_mean):
    """The single place that defines the Alfven-speed convention for M_A.

    Athena++ (mu = 1 code units): v_A = B0 / sqrt(rho_mean).  CATS: not defined -> NaN.
    """
    if code == "athenapp" and rho_mean > 0:
        return B0 / math.sqrt(rho_mean)
    return SENTINEL_FLOAT


# ================================================================== normalisation
@obs.traced("data_pipeline.normalise")
def normalise(rho, B, vel, code, cs, file, url):
    """Normalise a run and measure its properties.

    Returns (rho_n, B_n, props).  rho_n = rho / <rho>;  B_n = B / |<B>|.  ``props`` holds
    raw <rho>, |<B>|, the mean-field unit vector, dB/B0 = rms(B - <B>) / |<B>|, and M_s and
    M_A when velocities and a sound speed exist (else NaN, logged as optional fields).
    """
    rho_mean = float(rho.mean())
    mean = np.array([B[k].mean() for k in "xyz"])
    B0 = float(np.linalg.norm(mean))
    rho_n = rho / rho_mean
    B_n = {k: B[k] / B0 for k in "xyz"}
    dB = float(np.sqrt(sum(((B[k] - B[k].mean()) ** 2).mean() for k in "xyz")) / B0)
    props = {"rho_mean": rho_mean, "B0": B0, "unit": mean / B0, "dB_over_B0": dB,
             "M_s": SENTINEL_FLOAT, "M_A": SENTINEL_FLOAT}
    if vel is None:
        if code == "athenapp":
            note_missing("velocity (vel1..vel3)", file, url, SENTINEL_FLOAT)
    else:
        v_rms = float(np.sqrt(sum((vel[k] ** 2).mean() for k in "xyz")))
        if cs is None or not math.isfinite(cs):
            note_missing("iso_sound_speed", file, url, SENTINEL_FLOAT)
        else:
            props["M_s"] = v_rms / cs
        vA = alfven_speed(code, B0, rho_mean)
        if math.isfinite(vA) and vA > 0:
            props["M_A"] = v_rms / vA
    if code != "athenapp":
        note_missing("M_s", file, url, SENTINEL_FLOAT)          # CATS has no velocity cubes
        note_missing("M_A", file, url, SENTINEL_FLOAT)
    return rho_n, B_n, props


# ================================================================== estimation
def input_signature(shas, angle):
    """Signature of everything a map depends on: raw hashes, angle, code version."""
    s = json.dumps({"shas": shas, "angle": angle, "p0": fm.P0_TRUE, "v": PIPELINE_VERSION,
                    "firstmap_b": sha256_file(HERE / "firstmap_b.py"),
                    "rotate_maps": sha256_file(HERE / "rotate_maps.py")}, sort_keys=True)
    return sha256_bytes(s.encode())


def map_file(cache, sig):
    """Path of the cached map file for an input signature."""
    return cache / "maps" / f"{sig}.npy"


@obs.traced("data_pipeline.estimate_run")
def estimate_run(rho_n, B_n, shas, cache):
    """Run the Chen estimator at every angle and p_max rule for one normalised run.

    Maps (Q, U, p, cos^2 gamma) are cached as ``.npy`` files named by input signature, so
    reruns skip the expensive line-of-sight integration.  Returns (estimate rows,
    map index rows).  Uses ``rotate_maps.rotated_maps`` and
    ``firstmap_b.estimate_from_maps`` unchanged.
    """
    field_ax, perp_ax, mean = rm.choose_axes(B_n)
    rows, mrows = [], []
    for ang in ANGLES:
        sig = input_signature(shas, ang)
        mf = map_file(cache, sig)
        arr = None
        if mf.exists():
            try:
                arr = np.load(mf)
                if arr.shape[0] != 4:
                    arr = None
            except (ValueError, OSError, EOFError):
                arr = None                          # corrupt or half-written cache file: recompute
        if arr is not None:
            obs.event("map_cache_hit", angle=ang, map_file=mf)
            Q, U, p, cos2 = (arr[i].astype(np.float64) for i in range(4))
        else:
            _, Q, U, p, cos2 = obs.call("rotate_maps.rotated_maps", rm.rotated_maps,
                                        rho_n, B_n, ang, field_ax, perp_ax)
            mf.parent.mkdir(parents=True, exist_ok=True)
            tmp = mf.with_name(mf.name + ".tmp")
            with open(tmp, "wb") as fh:             # write, then rename: never a partial map
                np.save(fh, np.stack([Q, U, p, cos2]).astype(np.float64))
            os.replace(tmp, mf)
        mrows.append({"angle": ang, "input_signature": sig,
                      "map_content_sha256": sha256_file(mf)})
        truth = rm.true_gamma(mean, ang, field_ax, perp_ax)
        for rule, pct in PMAX_RULES.items():
            # the rule is passed explicitly (no longer by changing fm.PMAX_PERCENTILE)
            r = obs.call(f"firstmap_b.estimate_from_maps[{rule}]", fm.estimate_from_maps,
                         Q, U, p, cos2, truth, mask_border=True, pmax_percentile=pct)
            vals = {k: r[k] for k in RESULT_COLS}
            if not all(math.isfinite(vals[k]) for k in ("gamma_B_true", "gamma_obs", "gamma_obs_lowS")):
                log.warning("non-finite estimate at %d deg, rule %s: marked DATA_UNAVAILABLE", ang, rule)
                rows.append({"angle": ang, "rule": rule, "status": "DATA_UNAVAILABLE",
                             "field_axis": field_ax, **{k: None for k in RESULT_COLS}})
                continue
            rows.append({"angle": ang, "rule": rule, "status": "ok", "field_axis": field_ax, **vals})
    return rows, mrows


def refuse_rows(field_ax="UNKNOWN"):
    """Rows for a run whose required inputs are sentinels: DATA_UNAVAILABLE, no numbers."""
    return [{"angle": a, "rule": rule, "status": "DATA_UNAVAILABLE", "field_axis": field_ax,
             **{k: None for k in RESULT_COLS}} for a in ANGLES for rule in PMAX_RULES]


@obs.traced("data_pipeline.check_cubes")
def check_cubes(rho, B):
    """Return a list of required-input problems in the cubes (empty list = usable).

    Non-finite values (NaN/inf), a non-positive density and a zero mean field would make the
    estimator return garbage or crash, so such runs are refused instead.
    """
    problems = []
    if rho.ndim != 3 or len(set(rho.shape)) != 1:
        return [f"grid {tuple(rho.shape)} is not an n x n x n cube (rotate_maps needs a cube)"]
    for name, a in (("rho", rho), ("Bx", B["x"]), ("By", B["y"]), ("Bz", B["z"])):
        bad = int((~np.isfinite(a)).sum())
        if bad:
            problems.append(f"{name}: {bad} non-finite value(s)")
    if not problems:
        if float(rho.min()) <= 0.0:
            problems.append(f"rho: minimum {float(rho.min()):.3g} is not positive")
        b0 = float(np.linalg.norm([B[k].mean() for k in "xyz"]))
        rms = float(np.sqrt(sum(((B[k] - B[k].mean()) ** 2).mean() for k in "xyz")))
        if b0 == 0.0 or (rms > 0 and b0 < 1e-6 * rms):      # float noise counts as zero
            problems.append("B: mean field is zero (relative to fluctuations), tilt undefined")
    return problems


def refuse(row, prov, urls, status, m0, reason):
    """Mark a run unusable: set its status, list the missing fields, and record it in the
    provenance so a failed run is documented too.  Returns the DATA_UNAVAILABLE rows."""
    row["data_status"] = status
    names = sorted({e["field"] for e in MISSING[m0:]})
    row["missing_fields"] = ",".join(names)
    prov.data["datasets"][row["run_id"]] = {"source_urls": urls, "data_status": status,
                                            "reason": reason, "missing_fields": names}
    log.warning("%s: %s (%s): estimator refuses -> DATA_UNAVAILABLE", row["run_id"], status, reason)
    return refuse_rows()


# ================================================================== run builders
def dataset_stub(run_id, legacy, source, code, snapshot, url, status):
    """A dataset row with every field set to its sentinel (used for missing runs)."""
    return {"run_id": run_id, "legacy_name": legacy, "source": source, "code": code,
            "snapshot": snapshot, "source_url": url, "data_status": status,
            "grid_n": SENTINEL_COUNT, "sha256_rho": None, "sha256_bx": None,
            "sha256_by": None, "sha256_bz": None, "rho_mean": None, "B0": None,
            "ux": None, "uy": None, "uz": None, "field_axis": SENTINEL_STR,
            "dB_over_B0": None, "M_s": None, "M_A": None, "gamma_B_0deg": None,
            "bunit": SENTINEL_STR, "alfven_convention": SENTINEL_STR,
            "missing_fields": ""}


def finish_dataset(row, props, shas, n, bunit, convention, first_missing_idx, estimates):
    """Fill a dataset row from measured properties and the missing-field log slice."""
    row.update(grid_n=int(n), sha256_rho=shas.get("rho"), sha256_bx=shas.get("bx"),
               sha256_by=shas.get("by"), sha256_bz=shas.get("bz"),
               rho_mean=rnd(props["rho_mean"]), B0=rnd(props["B0"]),
               ux=rnd(props["unit"][0]), uy=rnd(props["unit"][1]), uz=rnd(props["unit"][2]),
               dB_over_B0=rnd(props["dB_over_B0"]), M_s=rnd(props["M_s"]), M_A=rnd(props["M_A"]),
               bunit=bunit, alfven_convention=convention)
    g0 = [e for e in estimates if e["angle"] == 0 and e["rule"] == "max"]
    if g0:
        row["gamma_B_0deg"] = rnd(g0[0]["gamma_B_true"])
        row["field_axis"] = g0[0]["field_axis"]
    names = sorted({e["field"] for e in MISSING[first_missing_idx:]})
    row["missing_fields"] = ",".join(names)
    return row


@obs.traced("data_pipeline.build_cats_run", skip_args=("prov",))
def build_cats_run(legacy, status, project, cache, prov):
    """Normalise and estimate one CATS run.  Returns (dataset row, estimates, map rows)."""
    run_folder, snap = CATS_RUNS[legacy]
    run_id = f"cho:{run_folder}:t{snap}"
    url = cats_url_of(find_local(project, run_folder, snap, "rho"), run_folder, snap, "rho")
    have = all(find_local(project, run_folder, snap, c) is not None for c in COMPONENTS)
    if not have:
        status = status if status in ("missing", "unreachable") else "missing"
    row = dataset_stub(run_id, legacy, "cats_cho_eno", "Cho-ENO", f"t_{snap}", url,
                       "ok" if have else status)
    m0 = len(MISSING)
    if not have:
        return row, refuse(row, prov, [url], status, m0, "files not available"), []
    for comp in COMPONENTS:                     # make sure every file has a provenance record
        key = f"{legacy}:{comp}"
        local = find_local(project, run_folder, snap, comp)
        rec = prov.data["files"].get(key)
        if not rec or rec.get("sha256") != sha256_file(local):
            prov.record_file(key, {"url": cats_url_of(local, run_folder, snap, comp),
                                   "path": relpath(local, project), "sha256": sha256_file(local),
                                   "bytes": local.stat().st_size, "access_time_utc": None,
                                   "local_file_modified_utc": mtime_utc(local),
                                   "acquired_by": "pre-existing local file (not downloaded by "
                                   "this pipeline run; original download time not recorded; "
                                   "modification time is only an approximation)"})
    try:
        rho, B, info = load_cats_cubes(legacy, project, prov)
    except Exception as e:                      # truncated gzip, bad FITS, shape mismatch ...
        note_missing("readable FITS cubes", f"{run_folder}/*_t{snap}", url, SENTINEL_STR, required=True)
        return row, refuse(row, prov, [url], "unusable", m0, f"{type(e).__name__}: {e}"), []
    problems = check_cubes(rho, B)
    if problems:
        for pr in problems:
            note_missing(pr, f"{run_folder}/*_t{snap}", url, SENTINEL_FLOAT, required=True)
        return row, refuse(row, prov, [url], "unusable", m0, "; ".join(problems)), []
    rho_n, B_n, props = normalise(rho, B, None, "cats_cho_eno", None,
                                  f"{run_folder}/*_t{snap}", url)
    n = rho.shape[0]
    del rho, B                                   # free ~0.5 GB before the map integration
    try:
        est, mrows = estimate_run(rho_n, B_n, info["shas"], cache)
    except Exception as e:                      # keep the pipeline alive; document the failure
        note_missing("estimator output", f"{run_folder}/*_t{snap}", url, SENTINEL_FLOAT, required=True)
        return row, refuse(row, prov, [url], "unusable", m0, f"estimator failed: {type(e).__name__}: {e}"), []
    row = finish_dataset(row, props, info["shas"], n, info["bunit"],
                         ALFVEN_CONVENTION["cats_cho_eno"], m0, est)
    prov.data["datasets"][run_id] = {
        "source_urls": [prov.data["files"][f"{legacy}:{c}"]["url"] for c in COMPONENTS],
        "files": {c: prov.data["files"][f"{legacy}:{c}"] for c in COMPONENTS},
        "version": "no version number published by CATS; file SHA-256 used as version",
        "fits_header_rho": info["header_text"],
        "transformations": TRANSFORMATIONS,
    }
    return row, est, mrows


@obs.traced("data_pipeline.build_athena_run", skip_args=("prov",))
def build_athena_run(athdf, meta_json, project, cache, prov, commit_rec):
    """Normalise and estimate the Athena++ run.  Returns (dataset row, estimates, maps)."""
    sha7 = ATHENA_SHA[:7]
    athdf = Path(athdf)
    url = f"https://github.com/{ATHENA_REPO}/tree/{ATHENA_SHA}"
    snap = re.search(r"(\d{5})\.athdf$", athdf.name)
    snap = snap.group(1) if snap else SENTINEL_STR
    run_id = f"athenapp:{sha7}:{athdf.stem}"
    legacy = f"athenapp_{sha7}_snap{int(snap) if snap.isdigit() else 'x'}"
    row = dataset_stub(run_id, legacy, "athenapp_own_run", "Athena++", snap, url, "ok")
    m0 = len(MISSING)
    if not athdf.exists():
        note_missing("athdf file", relpath(athdf, project), url, SENTINEL_STR, required=True)
        return row, refuse(row, prov, [url], "missing", m0, "Athena++ output file not found"), []
    meta = {}
    if meta_json and Path(meta_json).exists():
        try:
            meta = json.loads(Path(meta_json).read_text())
        except ValueError as e:
            note_missing("run metadata JSON", relpath(meta_json, project), url, SENTINEL_STR)
            log.warning("could not parse %s: %s", meta_json, e)
    file_sha = sha256_file(athdf)
    pinned = (meta.get("outputs") or {}).get("final_file_sha256")
    rec = {"url": url, "path": relpath(athdf, project), "sha256": file_sha, "bytes": athdf.stat().st_size,
           "access_time_utc": None, "local_file_modified_utc": mtime_utc(athdf),
           "ingested_time_utc": utc_now(),
           "acquired_by": "local output of my own Athena++ run (upstream artifact identified "
                          "by SHA-256; the simulation is reproducible to tolerance only)",
           "sha256_matches_recorded_run": (pinned == file_sha) if pinned else None}
    prov.record_file("athenapp:athdf", rec)
    try:
        rho, B, vel, ameta = read_athdf(athdf)
    except Exception as e:                      # not HDF5, missing attributes, refined mesh ...
        note_missing("readable .athdf", relpath(athdf, project), url, SENTINEL_STR, required=True)
        return row, refuse(row, prov, [url], "unusable", m0, f"{type(e).__name__}: {e}"), []
    if rho is None:
        note_missing("rho", athdf, url, SENTINEL_FLOAT, required=True)
    if B is None:
        for n in ("Bcc1", "Bcc2", "Bcc3"):
            note_missing(n, athdf, url, SENTINEL_FLOAT, required=True)
    if rho is None or B is None:
        return row, refuse(row, prov, [url], "unusable", m0, "required fields absent"), []
    problems = check_cubes(rho, B)
    if problems:
        for pr in problems:
            note_missing(pr, relpath(athdf, project), url, SENTINEL_FLOAT, required=True)
        return row, refuse(row, prov, [url], "unusable", m0, "; ".join(problems)), []
    cs = (meta.get("key_parameters") or {}).get("iso_sound_speed", None)
    rho_n, B_n, props = normalise(rho, B, vel, "athenapp", cs, str(athdf), url)
    shas = {"rho": file_sha, "bx": file_sha, "by": file_sha, "bz": file_sha}
    n = rho.shape[0]
    del rho, B, vel
    try:
        est, mrows = estimate_run(rho_n, B_n, shas, cache)
    except Exception as e:
        note_missing("estimator output", relpath(athdf, project), url, SENTINEL_FLOAT, required=True)
        return row, refuse(row, prov, [url], "unusable", m0, f"estimator failed: {type(e).__name__}: {e}"), []
    row = finish_dataset(row, props, shas, n, "code units (mu = 1)",
                         ALFVEN_CONVENTION["athenapp"], m0, est)
    prov.data["datasets"][run_id] = {
        "source_urls": [url, commit_rec.get("url")],
        "files": {"athdf": rec},
        "version": {"athenapp_commit": ATHENA_SHA, "file_sha256": file_sha,
                    "github_commit_record_status": commit_rec.get("status")},
        "run_metadata": meta,
        "snapshot_time_code_units": ameta["time"],
        "transformations": TRANSFORMATIONS + [
            "read .athdf with h5py: prim (rho, vel1-3) and B (Bcc1-3) datasets, single-level mesh",
            "M_s = v_rms / iso_sound_speed; M_A = v_rms / (|<B>| / sqrt(<rho>))  [mu = 1 units]"],
    }
    return row, est, mrows


TRANSFORMATIONS = [
    "rho normalised to <rho> = 1; B normalised to |<B>| (tilt is unit-free)",
    "dB/B0 = rms(B - <B>) / |<B>| over the full cube",
    "tilted line of sight: rotate_maps.rotated_maps (trilinear sampling, periodic wrap) at "
    "angles " + ",".join(map(str, ANGLES)),
    "Stokes Q, U, polarization fraction p with intrinsic p0 = 0.15 (Chen et al. 2019, eqs. 1-3)",
    "p0 derived from p_max (eq. 9) with p_max = map maximum ('max') or 99.5th percentile",
    "tilt inversion gamma = arccos(sqrt(cos^2 gamma)) from p (eq. 10)",
    "S cut: pixels with dispersion S below the median S ('low-S'); outer pixel ring masked",
    "KDE peak (Gaussian, Scott's rule) on a 0-90 degree grid",
]


# ================================================================== cache (SQLite)
@obs.traced("data_pipeline.write_cache", skip_args=("estimates", "maps"))
def write_cache(db_path, datasets, estimates, maps):
    """Write the SQLite cache from scratch: sorted rows, rounded floats, no timestamps."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    dcols = [r[1] for r in con.execute("PRAGMA table_info(datasets)")]
    ecols = [r[1] for r in con.execute("PRAGMA table_info(estimates)")]
    con.executemany(f"INSERT INTO datasets ({','.join(dcols)}) VALUES ({','.join('?' * len(dcols))})",
                    [tuple(d[c] for c in dcols) for d in sorted(datasets, key=lambda d: d["run_id"])])
    flat = [{"run_id": rid, **e} for rid, ests in estimates.items() for e in ests]
    flat.sort(key=lambda e: (e["run_id"], e["angle"], e["rule"]))
    con.executemany(f"INSERT INTO estimates ({','.join(ecols)}) VALUES ({','.join('?' * len(ecols))})",
                    [tuple(rnd(e[c]) if c in RESULT_COLS else e[c] for c in ecols) for e in flat])
    mflat = sorted(({"run_id": rid, **m} for rid, ms in maps.items() for m in ms),
                   key=lambda m: (m["run_id"], m["angle"]))
    con.executemany("INSERT INTO maps VALUES (?,?,?,?)",
                    [(m["run_id"], m["angle"], m["input_signature"], m["map_content_sha256"])
                     for m in mflat])
    con.commit()
    con.execute("VACUUM")
    con.close()
    os.replace(tmp, db_path)


@obs.traced("data_pipeline.canonical_hash")
def canonical_hash(db_path):
    """SHA-256 of the cache *content*: schema text plus every row of the canonical tables,
    serialised as sorted JSON.  Independent of the SQLite library version and of file
    layout; the ``maps`` table (machine-dependent float bits) is excluded.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    h = hashlib.sha256()
    for table, order in sorted(CANONICAL_TABLES.items()):
        sql = con.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
        h.update(sql.encode())
        cur = con.execute(f"SELECT * FROM {table} ORDER BY {order}")
        for row in cur:
            h.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode())
            h.update(b"\n")
    con.close()
    return h.hexdigest()


@obs.traced("data_pipeline.export_results_csv")
def export_results_csv(db_path, out_csv):
    """Write ``results_rotated.csv`` in the format ``build_db.py`` reads, so the existing
    MCP results.db can be fed from this cache.  Run names carry ``_pmax100`` for the
    map-maximum rule (the build_db convention).  Rows with status DATA_UNAVAILABLE are
    left out: they must never become numbers.
    """
    import csv
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    q = ("SELECT d.legacy_name, e.rule, e.angle, e.field_axis, "
         + ",".join(f"e.{c}" for c in RESULT_COLS) +
         " FROM estimates e JOIN datasets d USING(run_id) WHERE e.status='ok' "
         "ORDER BY d.legacy_name, e.rule, e.angle")
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run", "angle", "field_axis"] + RESULT_COLS)
        for name, rule, ang, fax, *vals in con.execute(q):
            w.writerow([name + ("_pmax100" if rule == "max" else ""), ang, fax] + vals)
    con.close()


# ================================================================== commands
@obs.traced("data_pipeline.cmd_fetch", skip_args=("prov",))
def cmd_fetch(args, project, prov):
    """--fetch: get CATS files (retries, 404 handling) and the GitHub commit record."""
    statuses = {}
    m0, r0 = len(MISSING), len(RETRY_EVENTS)
    for legacy in args.run_list:
        statuses[legacy] = fetch_cats_run(legacy, project, prov, force=args.force)
        log.info("CATS %s: %s", legacy, statuses[legacy])
    commit = {"status": "skipped"}
    if not args.no_github and not args.no_athena:
        raw = project / "cache" / "raw" / f"github_commit_{ATHENA_SHA[:7]}.json"
        old = prov.data["api_calls"].get("github_commit", {})
        if (not args.force and old.get("status") == "ok" and raw.exists()
                and old.get("sha256") == sha256_file(raw)):
            commit = old                         # cached: a commit's JSON never changes
            log.info("GitHub commit %s: using cached response (no API call)", ATHENA_SHA[:7])
        else:
            commit = github_commit(ATHENA_SHA, project / "cache" / "raw", prov)
            log.info("GitHub commit %s: %s", ATHENA_SHA[:7], commit["status"])
    prov.data["fetch_status"] = {"cats": statuses, "github_commit": commit.get("status")}
    prov.data["retry_log"] = list(RETRY_EVENTS[r0:])           # kept until the next --fetch
    prov.data["missing_field_log_fetch"] = list(MISSING[m0:])
    return statuses, commit


@obs.traced("data_pipeline.cmd_build", skip_args=("prov",))
def cmd_build(args, project, prov):
    """--build: normalise, estimate and write the SQLite cache and provenance hash."""
    cache = project / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    statuses = prov.data.get("fetch_status", {}).get("cats", {})
    prov.data["datasets"] = {}                  # rebuilt below: no stale entries from other run sets
    m0 = len(MISSING)
    datasets, estimates, maps = [], {}, {}
    for legacy in args.run_list:
        st = statuses.get(legacy, "ok")
        row, est, mrows = build_cats_run(legacy, st, project, cache, prov)
        datasets.append(row)
        estimates[row["run_id"]] = est
        maps[row["run_id"]] = mrows
    if not args.no_athena:
        commit = prov.data["api_calls"].get("github_commit", {"status": "not fetched"})
        row, est, mrows = build_athena_run(args.athena_file, args.athena_meta, project, cache,
                                           prov, commit)
        datasets.append(row)
        estimates[row["run_id"]] = est
        maps[row["run_id"]] = mrows
    prov.data["missing_field_log_build"] = list(MISSING[m0:])
    db = cache / "pipeline_cache_alexis.db"
    write_cache(db, datasets, estimates, maps)
    export_results_csv(db, cache / "results_rotated.csv")
    prov.data["cache"] = {
        "database": str(db.relative_to(project)),
        "canonical_content_sha256": canonical_hash(db),
        "hash_definition": "sha256 over the schema text and every row of tables datasets and "
                           "estimates (sorted, JSON, floats rounded to "
                           f"{FLOAT_DECIMALS} decimals); no timestamps; maps table excluded",
        "db_file_sha256_informational": sha256_file(db),
        "n_datasets": len(datasets),
        "statuses": {d["run_id"]: d["data_status"] for d in datasets},
        "results_csv_for_build_db": str((cache / "results_rotated.csv").relative_to(project)),
    }
    for d in datasets:
        log.info("%-40s %-12s dB/B0=%s M_A=%s missing=[%s]", d["run_id"], d["data_status"],
                 d["dB_over_B0"], d["M_A"], d["missing_fields"])


@obs.traced("data_pipeline.record_reproduction")
def record_reproduction(before, after):
    """Compare a fresh build with the cache hash that was recorded before it ran.

    ``--build`` overwrites the recorded hash, so without this step ``--build --verify`` would
    only compare the new cache with itself.  The previous hash and the outcome are kept in
    ``provenance["cache"]`` (``reproduces_previous_build`` is None when there was no previous
    record or the run set differs, because then the hashes are not comparable).
    """
    prev = before.get("canonical_content_sha256")
    same_runs = sorted(before.get("statuses", {})) == sorted(after.get("statuses", {}))
    after["previous_canonical_content_sha256"] = prev
    after["reproduces_previous_build"] = (prev == after["canonical_content_sha256"]
                                          if prev and same_runs else None)
    if prev is None:
        log.info("no previous cache hash recorded: nothing to compare the rebuild with")
    elif not same_runs:
        log.warning("previous cache hash is for a different run set; rebuild not compared "
                    "(pass the same runs, e.g. --all, to check reproduction)")
    else:
        log.info("rebuild %s the previously recorded cache hash",
                 "REPRODUCES" if after["reproduces_previous_build"] else "DOES NOT reproduce")


@obs.traced("data_pipeline.cmd_verify", skip_args=("prov",))
def cmd_verify(args, project, prov):
    """--verify: recompute the cache hash and compare it with provenance.  Returns 0/1."""
    ok = True
    db = project / "cache" / "pipeline_cache_alexis.db"
    rec = prov.data.get("cache", {})
    if not db.exists() or "canonical_content_sha256" not in rec:
        print("VERIFY FAILED: no cache or no recorded hash (run --build first)")
        return 1
    now = canonical_hash(db)
    same = now == rec["canonical_content_sha256"]
    print(f"cache content hash  now      {now}")
    print(f"                    recorded {rec['canonical_content_sha256']}")
    print("cache content:", "MATCH" if same else "MISMATCH")
    ok &= same
    rep = rec.get("reproduces_previous_build")
    if rep is not None:
        print("this build vs the hash recorded before it:",
              "REPRODUCED" if rep else f"NOT REPRODUCED (was {rec.get('previous_canonical_content_sha256')})")
        ok &= bool(rep)
    for key, f in sorted(prov.data["files"].items()):
        p = f.get("path")
        if p and f.get("sha256") and (project / p).exists():
            good = sha256_file(project / p) == f["sha256"]
            print(f"raw file {key:<28s}", "MATCH" if good else "MISMATCH (upstream data changed)")
            ok &= good
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    bad = 0
    for rid, ang, sig, content in con.execute("SELECT * FROM maps"):
        mf = map_file(project / "cache", sig)
        if mf.exists() and sha256_file(mf) != content:
            bad += 1
    con.close()
    print("map files (informational, may differ across machines):",
          "all match" if not bad else f"{bad} differ")
    print("VERIFY", "OK" if ok else "FAILED")
    return 0 if ok else 1


def parse_args(argv=None):
    """Command-line interface."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", action="store_true", help="download CATS files + GitHub commit JSON")
    ap.add_argument("--build", action="store_true", help="normalise, estimate, write cache + provenance")
    ap.add_argument("--verify", action="store_true", help="compare cache hash with provenance")
    ap.add_argument("--project", default=str(HERE), help="folder holding run folders, cache/, provenance")
    ap.add_argument("--runs", nargs="+", default=None, choices=sorted(CATS_RUNS),
                    help=f"CATS runs (default: {' '.join(DEFAULT_CATS_RUNS)})")
    ap.add_argument("--all", action="store_true",
                    help="all four CATS Cho-ENO runs, every snapshot in CATS_SNAPSHOTS (12 cubes sets)")
    ap.add_argument("--primary", action="store_true",
                    help="all four CATS runs, primary snapshot only (the HW9 run set)")
    ap.add_argument("--athena-file", default="data/athena/Turb.out2.00050.athdf")
    ap.add_argument("--athena-meta", default="data/athena/athena_run_provenance.json")
    ap.add_argument("--source", choices=["both", "cats", "athena"], default="both",
                    help="which source(s) to use: CATS site downloads, my Athena++ run, or both "
                         "(default)")
    ap.add_argument("--no-athena", action="store_true", help="same as --source cats")
    ap.add_argument("--no-github", action="store_true", help="skip the GitHub API call")
    ap.add_argument("--force", action="store_true", help="re-download even if files exist")
    ap.add_argument("--provenance", default="provenance_alexis.json")
    ap.add_argument("--log-file", default=None,
                    help="JSON-lines tool-call log (default: $OBS_LOG_FILE, else "
                         "<project>/logs/pipeline_alexis.jsonl)")
    a = ap.parse_args(argv)
    a.run_list = (sorted(CATS_RUNS) if a.all else PRIMARY_CATS_RUNS if a.primary
                  else (a.runs or DEFAULT_CATS_RUNS))
    if a.source == "cats":
        a.no_athena = True
    if a.source == "athena":
        a.run_list = []                         # no CATS runs
    return a


def main(argv=None):
    """Entry point.  Returns a process exit code."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stderr)
    if not (args.fetch or args.build or args.verify):
        print("nothing to do: give --fetch, --build and/or --verify (see --help)")
        return 2
    project = Path(args.project).resolve()
    global PROJECT_ROOT
    PROJECT_ROOT = project
    obs.configure("pipeline", log_file=args.log_file, project_root=project)
    args.athena_file = (project / args.athena_file) if not Path(args.athena_file).is_absolute() \
        else Path(args.athena_file)
    args.athena_meta = (project / args.athena_meta) if not Path(args.athena_meta).is_absolute() \
        else Path(args.athena_meta)
    prov = Provenance(project / args.provenance)
    code = 0
    if args.fetch:
        cmd_fetch(args, project, prov)
    if args.build:
        before = dict(prov.data.get("cache", {}))
        cmd_build(args, project, prov)
        record_reproduction(before, prov.data["cache"])
    if args.fetch or args.build:
        prov.save()
    if args.verify:
        code = cmd_verify(args, project, prov)
    return code


if __name__ == "__main__":
    sys.exit(main())
