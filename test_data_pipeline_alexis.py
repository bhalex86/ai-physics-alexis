"""
test_data_pipeline_alexis.py -- offline tests of data_pipeline_alexis.py.

A local HTTP server stands in for the CATS archive and the GitHub API and injects the
failures the pipeline must survive: 503, 429 with Retry-After, a rate-limited GitHub 403,
a hanging server (timeout), and 404.  Synthetic 32^3 cubes are used for CATS; a small
real ``.athdf`` file is needed for the Athena++ test (set ATHDF to its path).

    pytest test_data_pipeline_alexis.py -v
"""

import gzip
import hashlib
import io
import json
import os
import shutil
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import h5py
import numpy as np
import pytest
from astropy.io import fits

import data_pipeline_alexis as pl
import firstmap_b as fm

ATHDF = Path(os.environ.get("ATHDF", "test_data/Turb.out2.00005.athdf"))
SHA = pl.ATHENA_SHA


# ------------------------------------------------------------------ fake servers
def fits_bytes(arr, header_extra=None, gz=True):
    """Serialise an array as a FITS file (optionally gzipped)."""
    hdu = fits.PrimaryHDU(arr.astype(np.float32))
    for k, v in (header_extra or {}).items():
        hdu.header[k] = v
    buf = io.BytesIO()
    hdu.writeto(buf)
    data = buf.getvalue()
    return gzip.compress(data, mtime=0) if gz else data


class State:
    """Shared, thread-safe request counters and the files the fake server hands out."""
    lock = threading.Lock()
    counts = {}
    files = {}
    gh_calls = 0


class Handler(BaseHTTPRequestHandler):
    """Fake CATS + GitHub server with injected failures."""

    def log_message(self, *a):
        """Silence request logging."""

    def _send(self, code, body=b"", headers=None):
        """Send a response."""
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        """Route: GitHub commit endpoint, or a CATS file path."""
        path = self.path
        with State.lock:
            State.counts[path] = State.counts.get(path, 0) + 1
            n = State.counts[path]
        if path.startswith("/repos/"):
            State.gh_calls += 1
            if n == 1:   # rate limited once
                return self._send(403, b"{}", {"X-RateLimit-Remaining": "0",
                                               "X-RateLimit-Reset": str(int(time.time()) + 1)})
            body = json.dumps({"sha": SHA, "commit": {"message": "Pin\nmore",
                               "committer": {"date": "2024-01-01T00:00:00Z"}}}).encode()
            return self._send(200, body)
        if "/b1p.01/t_900/" in path:          # server hangs -> client timeout
            time.sleep(3)
            return self._send(200, b"x")
        if "/b1p1/t_900/" in path:            # missing run
            return self._send(404)
        if "magx_t750.fits.gz" in path and n <= 2:
            return self._send(503)
        if "magy_t750.fits.gz" in path and n == 1:
            return self._send(429, b"", {"Retry-After": "1"})
        if path in State.files:
            return self._send(200, State.files[path], {"Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT",
                                                       "ETag": '"abc"'})
        return self._send(404)


@pytest.fixture(scope="module")
def server():
    """Start the fake server and serve a synthetic Cho-ENO run b.1p.01 snapshot 750."""
    rho, B = fm.fake_cube(n=32, seed=3)
    base = "/MHD/256/b.1p.01/t_750/"
    State.files = {
        base + "dens_t750.fits": fits_bytes(rho, gz=False),            # uncompressed density
        base + "magx_t750.fits.gz": fits_bytes(B["x"], {"BUNIT": "uG"}),
        base + "magy_t750.fits.gz": fits_bytes(B["y"], {"BUNIT": "uG"}),
        base + "magz_t750.fits.gz": fits_bytes(B["z"]),                # no BUNIT: missing field
    }
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture()
def fast(monkeypatch, server):
    """Point the pipeline at the fake server, with short backoff and timeouts."""
    monkeypatch.setattr(pl, "CATS_BASE_URL", server + "/")
    monkeypatch.setattr(pl, "GITHUB_API_URL", server)
    monkeypatch.setattr(pl, "BACKOFF_INITIAL", 0.05)
    monkeypatch.setattr(pl, "BACKOFF_JITTER", 0.05)
    monkeypatch.setattr(pl, "STAT_TIMEOUT", (2, 1))
    State.counts.clear()
    State.gh_calls = 0
    pl.MISSING.clear()
    pl.RETRY_EVENTS.clear()


# ------------------------------------------------------------------ failure mode (a)
def test_retries_503_429_and_404_not_retried(fast, tmp_path, server):
    """503 x2 and 429 are retried to success; a 404 is called exactly once."""
    prov = pl.Provenance(tmp_path / "p.json")
    st = pl.fetch_cats_run("b.1p.01_t750", tmp_path, prov)
    assert st == "ok"
    assert State.counts["/MHD/256/b.1p.01/t_750/magx_t750.fits.gz"] == 3   # 2 failures + success
    assert State.counts["/MHD/256/b.1p.01/t_750/magy_t750.fits.gz"] == 2   # 429 + success
    # density: .fits.gz is 404 (once, no retry) then the uncompressed file is found
    assert State.counts["/MHD/256/b.1p.01/t_750/dens_t750.fits.gz"] == 1
    assert State.counts["/MHD/256/b.1p.01/t_750/dens_t750.fits"] == 1
    assert any(e["wait_s"] >= 1.0 for e in pl.RETRY_EVENTS)               # Retry-After honoured
    assert prov.data["files"]["b.1p.01_t750:rho"]["http_headers"]["ETag"] == '"abc"'


def test_missing_run_is_recorded_not_crashing(fast, tmp_path):
    """A 404 run becomes data_status=missing and DATA_UNAVAILABLE, with one request."""
    prov = pl.Provenance(tmp_path / "p.json")
    assert pl.fetch_cats_run("b1p1_t900", tmp_path, prov) == "missing"
    assert State.counts["/MHD/256/b1p1/t_900/dens_t900.fits.gz"] == 1
    row, est, maps = pl.build_cats_run("b1p1_t900", "missing", tmp_path, tmp_path / "cache", prov)
    assert row["data_status"] == "missing" and maps == []
    assert all(e["status"] == "DATA_UNAVAILABLE" and e["gamma_obs"] is None for e in est)


def test_timeout_gives_up_after_four_attempts(fast, tmp_path):
    """A hanging server is retried 3 times (4 attempts) and then reported as unreachable."""
    prov = pl.Provenance(tmp_path / "p.json")
    t0 = time.time()
    assert pl.fetch_cats_run("b1p.01_t900", tmp_path, prov) == "unreachable"
    assert len(pl.RETRY_EVENTS) == 3                       # 3 retries ...
    assert State.counts["/MHD/256/b1p.01/t_900/dens_t900.fits.gz"] == 4   # ... = 4 attempts
    assert time.time() - t0 < 30


def test_github_rate_limit_403_is_retried(fast, tmp_path):
    """A 403 with X-RateLimit-Remaining: 0 is retried, then the commit JSON is stored."""
    prov = pl.Provenance(tmp_path / "p.json")
    rec = pl.github_commit(SHA, tmp_path / "raw", prov)
    assert rec["status"] == "ok" and rec["commit_sha"] == SHA
    assert (tmp_path / "raw" / f"github_commit_{SHA[:7]}.json").exists()


def test_github_unreachable_does_not_crash(fast, tmp_path, monkeypatch):
    """With nothing listening, the GitHub call degrades to status=unreachable."""
    monkeypatch.setattr(pl, "GITHUB_API_URL", "http://127.0.0.1:9")
    prov = pl.Provenance(tmp_path / "p.json")
    rec = pl.github_commit(SHA, tmp_path / "raw", prov)
    assert rec["status"] == "unreachable" and rec["commit_sha"] == pl.SENTINEL_STR
    assert len(pl.RETRY_EVENTS) == 3


# ------------------------------------------------------------------ failure mode (b)
def test_missing_header_field_logged_with_sentinel(fast, tmp_path):
    """A FITS file without BUNIT is logged (field, file, URL) and gets 'UNKNOWN'."""
    prov = pl.Provenance(tmp_path / "p.json")
    pl.fetch_cats_run("b.1p.01_t750", tmp_path, prov)
    pl.MISSING.clear()
    row, est, maps = pl.build_cats_run("b.1p.01_t750", "ok", tmp_path, tmp_path / "cache", prov)
    assert row["bunit"] == pl.SENTINEL_STR
    ev = [e for e in pl.MISSING if e["field"] == "BUNIT"][0]
    assert ev["source_url"].startswith("http") and ev["file"].endswith("dens_t750.fits")
    assert "BUNIT" in row["missing_fields"] and "M_A" in row["missing_fields"]
    assert row["M_A"] is None and row["data_status"] == "ok"      # optional field: still estimated
    assert all(e["status"] == "ok" for e in est)


@pytest.mark.skipif(not ATHDF.exists(), reason="needs a real .athdf file (ATHDF=...)")
def test_athena_without_B_is_refused(fast, tmp_path):
    """An Athena++ file without B data is unusable: DATA_UNAVAILABLE, never a number."""
    bad = tmp_path / "Turb.out2.00009.athdf"
    shutil.copy(ATHDF, bad)
    with h5py.File(bad, "r+") as f:
        del f["B"]
        f.attrs["DatasetNames"] = np.array([b"prim"])
        f.attrs["NumVariables"] = np.array([4], dtype=f.attrs["NumVariables"].dtype)
        f.attrs["VariableNames"] = f.attrs["VariableNames"][:4]
    prov = pl.Provenance(tmp_path / "p.json")
    row, est, maps = pl.build_athena_run(bad, None, tmp_path, tmp_path / "cache", prov,
                                         {"status": "skipped"})
    assert row["data_status"] == "unusable"
    assert {"Bcc1", "Bcc2", "Bcc3"} <= set(row["missing_fields"].split(","))
    assert all(e["status"] == "DATA_UNAVAILABLE" and e["gamma_obs_lowS"] is None for e in est)


# ------------------------------------------------------------------ end to end + reproducibility
@pytest.mark.skipif(not ATHDF.exists(), reason="needs a real .athdf file (ATHDF=...)")
def test_end_to_end_byte_identical_and_verify(fast, tmp_path, capsys):
    """Fetch+build twice (second time from a 'fresh clone' that has only the raw files and
    the provenance): identical cache content hash; --verify passes; tampering is caught."""
    def project(name):
        p = tmp_path / name
        (p / "data" / "athena").mkdir(parents=True)
        shutil.copy(ATHDF, p / "data" / "athena" / "Turb.out2.00050.athdf")
        (p / "data" / "athena" / "athena_run_provenance.json").write_text(json.dumps(
            {"key_parameters": {"iso_sound_speed": 1.0},
             "outputs": {"final_file_sha256": pl.sha256_file(ATHDF)}}))
        return p

    a = project("a")
    assert pl.main(["--project", str(a), "--fetch", "--build", "--verify"]) == 0
    prov_a = json.loads((a / "provenance_alexis.json").read_text())
    h_a = prov_a["cache"]["canonical_content_sha256"]
    assert prov_a["api_calls"]["github_commit"]["status"] == "ok"
    ids = sorted(prov_a["cache"]["statuses"])
    assert ids[0].startswith("athenapp:1395e2f") and ids[1] == "cho:b.1p.01:t750"

    # a fresh clone: new folder, raw files only, same provenance file committed
    b = project("b")
    for f in ("b.1p.01",):
        shutil.copytree(a / f, b / f)
    shutil.copy(a / "provenance_alexis.json", b / "provenance_alexis.json")
    assert pl.main(["--project", str(b), "--build", "--no-github", "--verify"]) == 0
    prov_b = json.loads((b / "provenance_alexis.json").read_text())
    assert prov_b["cache"]["canonical_content_sha256"] == h_a

    # no timestamps anywhere in the cache
    con = sqlite3.connect(a / "cache" / "pipeline_cache_alexis.db")
    dump = "\n".join(str(r) for t in ("datasets", "estimates") for r in con.execute(f"SELECT * FROM {t}"))
    assert "T00:" not in dump and "2026" not in dump and "access" not in dump.lower()
    # access time exists in provenance
    assert prov_a["files"]["b.1p.01_t750:rho"]["access_time_utc"].endswith("Z")

    # tamper with the cache: --verify must fail
    con.close()
    con = sqlite3.connect(a / "cache" / "pipeline_cache_alexis.db")
    con.execute("UPDATE estimates SET gamma_obs_lowS = gamma_obs_lowS + 1 WHERE angle = 45")
    con.commit(); con.close()
    assert pl.main(["--project", str(a), "--verify"]) == 1


def test_sentinel_helpers():
    """The sentinel helpers behave as documented."""
    assert pl.rnd(float("nan")) is None and pl.rnd(1.23456789) == 1.234568
    assert pl.is_sentinel(None) and pl.is_sentinel("UNKNOWN") and not pl.is_sentinel(0.0)


# ------------------------------------------------------------------ audit additions
class TruncHandler(BaseHTTPRequestHandler):
    """Sends a file whose body is cut short for the first ``fail_first`` requests."""
    fail_first = 2
    n = 0
    body = b""

    def log_message(self, *a):
        """Silence request logging."""

    def do_GET(self):
        """Announce the full length but send only part of it while failing."""
        TruncHandler.n += 1
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        if TruncHandler.n <= TruncHandler.fail_first:
            self.wfile.write(self.body[:len(self.body) // 3])
            self.wfile.flush()
            self.close_connection = True
        else:
            self.wfile.write(self.body)


def _trunc_server(fail_first):
    """Start a server that truncates its first ``fail_first`` responses."""
    TruncHandler.fail_first, TruncHandler.n = fail_first, 0
    TruncHandler.body = os.urandom(5000)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), TruncHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/f.fits.gz"


def test_cut_off_download_is_retried_and_file_is_complete(fast, tmp_path):
    """A transfer cut off twice is retried; the saved file is the complete, correct one."""
    srv, url = _trunc_server(fail_first=2)
    rec = pl.download(url, tmp_path / "f.fits.gz", tmp_path)
    srv.shutdown()
    assert TruncHandler.n == 3 and rec["bytes"] == 5000
    assert rec["sha256"] == hashlib.sha256(TruncHandler.body).hexdigest()
    assert not list(tmp_path.glob("*.part"))


def test_always_cut_off_download_does_not_crash(fast, tmp_path):
    """A transfer that never completes ends as 'unreachable', no exception, no partial file."""
    srv, url = _trunc_server(fail_first=99)
    prov = pl.Provenance(tmp_path / "p.json")
    m_urls = lambda *a: [(url, "f.fits.gz")]
    old = pl.cats_urls
    pl.cats_urls = m_urls
    try:
        assert pl.fetch_cats_run("b.1p.01_t750", tmp_path, prov) == "unreachable"
    finally:
        pl.cats_urls = old
        srv.shutdown()
    assert TruncHandler.n == 4                     # 4 attempts
    assert not list(tmp_path.rglob("*.part")) and not list(tmp_path.rglob("*.fits.gz"))


def _write_cubes(folder, rho, B, snap=750):
    """Write four FITS cubes the way the CATS layout names them."""
    folder.mkdir(parents=True, exist_ok=True)
    for stem, arr in (("dens", rho), ("magx", B["x"]), ("magy", B["y"]), ("magz", B["z"])):
        fits.PrimaryHDU(arr.astype(np.float32)).writeto(folder / f"{stem}_t{snap}.fits", overwrite=True)


@pytest.mark.parametrize("what", ["nan_in_B", "negative_density", "zero_mean_field"])
def test_bad_cube_values_are_refused(tmp_path, what):
    """NaN values, non-positive density or a zero mean field -> DATA_UNAVAILABLE, no crash."""
    rho, B = fm.fake_cube(n=16, seed=1)
    if what == "nan_in_B":
        B["y"][3, 3, 3] = np.nan
    elif what == "negative_density":
        rho[0, 0, 0] = -1.0
    else:
        B = {k: v - v.mean() for k, v in B.items()}
    _write_cubes(tmp_path / "b.1p.01", rho, B)
    pl.MISSING.clear()
    prov = pl.Provenance(tmp_path / "p.json")
    row, est, maps = pl.build_cats_run("b.1p.01_t750", "ok", tmp_path, tmp_path / "cache", prov)
    assert row["data_status"] == "unusable" and maps == []
    assert all(e["status"] == "DATA_UNAVAILABLE" and e["gamma_obs"] is None for e in est)
    assert "cho:b.1p.01:t750" in prov.data["datasets"]             # the failed run is documented
    assert prov.data["datasets"]["cho:b.1p.01:t750"]["data_status"] == "unusable"


def test_corrupt_files_do_not_crash(tmp_path):
    """A truncated .gz, and a text file posing as .athdf, are both refused cleanly."""
    d = tmp_path / "b.1p.01"
    d.mkdir()
    for stem in ("dens", "magx", "magy", "magz"):
        (d / f"{stem}_t750.fits.gz").write_bytes(gzip.compress(b"not a fits file")[:10])
    prov = pl.Provenance(tmp_path / "p.json")
    row, est, _ = pl.build_cats_run("b.1p.01_t750", "ok", tmp_path, tmp_path / "cache", prov)
    assert row["data_status"] == "unusable" and est[0]["status"] == "DATA_UNAVAILABLE"
    bad = tmp_path / "Turb.out2.00001.athdf"
    bad.write_text("this is not hdf5")
    row, est, _ = pl.build_athena_run(bad, None, tmp_path, tmp_path / "cache", prov, {"status": "skipped"})
    assert row["data_status"] == "unusable" and est[0]["status"] == "DATA_UNAVAILABLE"
    row, est, _ = pl.build_athena_run(tmp_path / "nope.athdf", None, tmp_path, tmp_path / "cache", prov,
                                      {"status": "skipped"})
    assert row["data_status"] == "missing"
    assert not any(isinstance(v, float) and v == v for e in est for k, v in e.items() if k in pl.RESULT_COLS)


def test_normalisation_does_not_change_the_estimate():
    """Dividing rho by <rho> and B by |<B>| must not change the estimator (Chen eqs. are ratios)."""
    import rotate_maps as rm
    rho, B = fm.fake_cube(n=24, seed=5)
    rho, B = rho * 7.3, {k: v * 3.1 for k, v in B.items()}
    mean = np.array([B[k].mean() for k in "xyz"])
    B0 = np.linalg.norm(mean)

    def lowS(r, b):
        fa, pa, mn = rm.choose_axes(b)
        _, Q, U, p, c = rm.rotated_maps(r, b, 45, fa, pa)
        return fm.estimate_from_maps(Q, U, p, c, rm.true_gamma(mn, 45, fa, pa), mask_border=True)["gamma_obs_lowS"]
    assert abs(lowS(rho, B) - lowS(rho / rho.mean(), {k: v / B0 for k, v in B.items()})) < 0.11


def test_exported_csv_feeds_build_db(tmp_path):
    """cache/results_rotated.csv must be accepted by the existing build_db.py (MCP results.db)."""
    import build_db
    rho, B = fm.fake_cube(n=16, seed=2)
    _write_cubes(tmp_path / "b.1p.01", rho, B)
    prov = pl.Provenance(tmp_path / "p.json")
    row, est, maps = pl.build_cats_run("b.1p.01_t750", "ok", tmp_path, tmp_path / "cache", prov)
    pl.write_cache(tmp_path / "c.db", [row], {row["run_id"]: est}, {row["run_id"]: maps})
    pl.export_results_csv(tmp_path / "c.db", tmp_path / "r.csv")
    out = build_db.build(str(tmp_path / "r.csv"), str(tmp_path / "none.csv"), str(tmp_path / "results.db"),
                         verbose=False)
    con = sqlite3.connect(out)
    assert con.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 2 * len(pl.ANGLES)  # angles x 2 rules
    assert {r[0] for r in con.execute("SELECT DISTINCT rule FROM results")} == {"max", "p99.5"}


def test_non_cubic_grid_is_refused(tmp_path):
    """rotate_maps needs an n x n x n cube; anything else is refused cleanly."""
    rho = np.ones((8, 8, 4))
    _write_cubes(tmp_path / "b.1p.01", rho, {"x": rho + 1, "y": rho * 0 + 0.1, "z": rho * 0 + 0.1})
    prov = pl.Provenance(tmp_path / "p.json")
    row, est, maps = pl.build_cats_run("b.1p.01_t750", "ok", tmp_path, tmp_path / "cache", prov)
    assert row["data_status"] == "unusable" and "cube" in prov.data["datasets"][row["run_id"]]["reason"]


def test_corrupt_cached_map_is_recomputed(tmp_path):
    """A half-written .npy in the map cache must not crash the build; it is recomputed."""
    rho, B = fm.fake_cube(n=16, seed=4)
    _write_cubes(tmp_path / "b.1p.01", rho, B)
    prov = pl.Provenance(tmp_path / "p.json")
    pl.build_cats_run("b.1p.01_t750", "ok", tmp_path, tmp_path / "cache", prov)
    maps = sorted((tmp_path / "cache" / "maps").glob("*.npy"))
    assert len(maps) == len(pl.ANGLES) and not list((tmp_path / "cache" / "maps").glob("*.tmp"))
    maps[0].write_bytes(b"garbage")
    row, est, _ = pl.build_cats_run("b.1p.01_t750", "ok", tmp_path, tmp_path / "cache", prov)
    assert row["data_status"] == "ok" and all(e["status"] == "ok" for e in est)


def test_non_finite_estimate_is_not_stored_as_ok(tmp_path, monkeypatch):
    """If the estimator returns NaN, the row must be DATA_UNAVAILABLE, never an 'ok' NaN."""
    rho, B = fm.fake_cube(n=16, seed=6)
    _write_cubes(tmp_path / "b.1p.01", rho, B)
    real = fm.estimate_from_maps

    def nan_estimate(*a, **k):
        r = real(*a, **k)
        r["gamma_obs_lowS"] = float("nan")
        return r
    monkeypatch.setattr(fm, "estimate_from_maps", nan_estimate)
    prov = pl.Provenance(tmp_path / "p.json")
    row, est, _ = pl.build_cats_run("b.1p.01_t750", "ok", tmp_path, tmp_path / "cache", prov)
    assert all(e["status"] == "DATA_UNAVAILABLE" and e["gamma_obs"] is None for e in est)


def test_github_non_json_and_secondary_rate_limit(fast, tmp_path, monkeypatch):
    """A 200 with HTML instead of JSON is handled; a 403 carrying Retry-After is retried."""
    class GH(BaseHTTPRequestHandler):
        n = 0

        def log_message(self, *a):
            """Silence request logging."""

        def do_GET(self):
            """First call: secondary rate limit; later calls: an HTML page."""
            GH.n += 1
            body = b"<html>captive portal</html>"
            self.send_response(403 if GH.n == 1 else 200)
            if GH.n == 1:
                self.send_header("Retry-After", "1")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), GH)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(pl, "GITHUB_API_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    prov = pl.Provenance(tmp_path / "p.json")
    rec = pl.github_commit(SHA, tmp_path / "raw", prov)
    srv.shutdown()
    assert GH.n == 2 and rec["status"] == "unparseable_response" and rec["commit_sha"] == pl.SENTINEL_STR
    assert not list((tmp_path / "raw").glob("*.json"))


@pytest.mark.skipif(not ATHDF.exists(), reason="needs a real .athdf file (ATHDF=...)")
def test_fetch_logs_survive_a_later_build(fast, tmp_path):
    """The retry log written by --fetch must still be in provenance after a separate --build."""
    proj = tmp_path / "p"
    (proj / "data" / "athena").mkdir(parents=True)
    shutil.copy(ATHDF, proj / "data" / "athena" / "Turb.out2.00050.athdf")
    assert pl.main(["--project", str(proj), "--fetch"]) == 0
    first = json.loads((proj / "provenance_alexis.json").read_text())
    assert len(first["retry_log"]) >= 3                       # 503 x2, 429, GitHub 403 were retried
    assert pl.main(["--project", str(proj), "--build", "--no-github"]) == 0
    second = json.loads((proj / "provenance_alexis.json").read_text())
    assert second["retry_log"] == first["retry_log"]
    assert second["fetch_status"] == first["fetch_status"]
    assert "missing_field_log_build" in second and "missing_field_log_fetch" in second


@pytest.mark.skipif(not ATHDF.exists(), reason="needs a real .athdf file (ATHDF=...)")
def test_source_switch(fast, tmp_path):
    """--source cats uses only CATS (no GitHub call, no Athena row); --source athena only Athena."""
    a = tmp_path / "a"
    assert pl.main(["--project", str(a), "--fetch", "--build", "--source", "cats"]) == 0
    prov = json.loads((a / "provenance_alexis.json").read_text())
    assert list(prov["cache"]["statuses"]) == ["cho:b.1p.01:t750"] and State.gh_calls == 0
    assert "github_commit" not in prov["api_calls"]
    b = tmp_path / "b"
    (b / "data" / "athena").mkdir(parents=True)
    shutil.copy(ATHDF, b / "data" / "athena" / "Turb.out2.00050.athdf")
    assert pl.main(["--project", str(b), "--build", "--source", "athena", "--no-github"]) == 0
    prov = json.loads((b / "provenance_alexis.json").read_text())
    assert list(prov["cache"]["statuses"]) == ["athenapp:1395e2f:Turb.out2.00050"]


def test_github_response_is_cached(fast, tmp_path):
    """A second --fetch must not call the GitHub API again; --force does."""
    proj = tmp_path / "p"
    args = ["--project", str(proj), "--fetch", "--source", "athena"]
    assert pl.main(args) == 0
    assert State.gh_calls == 2                    # 403 rate limit, then 200
    assert pl.main(args) == 0
    assert State.gh_calls == 2                    # served from the cache
    State.counts.clear()
    assert pl.main(args + ["--force"]) == 0
    assert State.gh_calls > 2


def test_rebuild_is_compared_with_the_previous_hash():
    """--build must compare its hash with the one recorded before it, not with itself."""
    runs = {"a": "ok"}
    after = {"canonical_content_sha256": "h1", "statuses": runs}
    pl.record_reproduction({"canonical_content_sha256": "h1", "statuses": runs}, after)
    assert after["reproduces_previous_build"] is True
    after = {"canonical_content_sha256": "h2", "statuses": runs}
    pl.record_reproduction({"canonical_content_sha256": "h1", "statuses": runs}, after)
    assert after["reproduces_previous_build"] is False
    after = {"canonical_content_sha256": "h2", "statuses": {"b": "ok"}}
    pl.record_reproduction({"canonical_content_sha256": "h1", "statuses": runs}, after)
    assert after["reproduces_previous_build"] is None          # different run set: not comparable
    after = {"canonical_content_sha256": "h2", "statuses": runs}
    pl.record_reproduction({}, after)
    assert after["reproduces_previous_build"] is None          # nothing recorded before


def test_package_versions_are_all_known():
    """Every package that affects the numbers reports a real version (tenacity has no __version__)."""
    v = pl._package_versions()
    assert v["tenacity"] != pl.SENTINEL_STR and v["numpy"] != pl.SENTINEL_STR


def test_symlinked_run_folder_stays_project_relative(tmp_path):
    """Provenance paths stay portable when a run folder is a symlink to data elsewhere."""
    data = tmp_path / "elsewhere" / "b1p1"
    data.mkdir(parents=True)
    (data / "dens_t900.fits.gz").write_bytes(b"x")
    project = tmp_path / "project"
    project.mkdir()
    (project / "b1p1").symlink_to(data, target_is_directory=True)
    assert pl.relpath(project / "b1p1" / "dens_t900.fits.gz", project) == "b1p1/dens_t900.fits.gz"
    assert pl.relpath(tmp_path / "other.txt", project) == str(tmp_path / "other.txt")


# ------------------------------------------------------------------ HW10 regression tests
def test_source_url_is_the_url_of_the_file_actually_used(fast, tmp_path, server):
    """BUG_LOG.md bug 1: the density of b.1p.01 t750 exists only as dens_t750.fits (the
    .fits.gz URL is a 404).  Every recorded URL must be the one that serves the file on
    disk: in the files record of a pre-existing local file, in the cache's
    datasets.source_url, and in the missing-field log."""
    import requests
    prov = pl.Provenance(tmp_path / "p.json")
    assert pl.fetch_cats_run("b.1p.01_t750", tmp_path, prov) == "ok"
    local = tmp_path / "b.1p.01" / "dens_t750.fits"
    assert local.exists()

    # second run on the same folder: the cubes are now pre-existing local files,
    # the layout the README asks users with already-downloaded data to use
    prov2 = pl.Provenance(tmp_path / "p2.json")
    assert pl.fetch_cats_run("b.1p.01_t750", tmp_path, prov2) == "ok"
    assert prov2.data["files"]["b.1p.01_t750:rho"]["url"].endswith("/dens_t750.fits")
    pl.MISSING.clear()
    row, est, maps = pl.build_cats_run("b.1p.01_t750", "ok", tmp_path, tmp_path / "cache", prov2)
    assert row["source_url"].endswith("/dens_t750.fits")
    bunit = [e for e in pl.MISSING if e["field"] == "BUNIT"][0]
    assert bunit["source_url"] == row["source_url"]

    # and the recorded URL really serves the bytes that were used
    r = requests.get(row["source_url"], timeout=5)
    assert r.status_code == 200
    assert hashlib.sha256(r.content).hexdigest() == row["sha256_rho"] == pl.sha256_file(local)
    for comp in pl.COMPONENTS:
        rec = prov2.data["files"][f"b.1p.01_t750:{comp}"]
        assert rec["url"].rsplit("/", 1)[1] == Path(rec["path"]).name
