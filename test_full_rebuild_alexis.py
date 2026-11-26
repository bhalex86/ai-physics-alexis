"""Tests of check_full_rebuild_alexis.py on synthetic CATS cubes (offline, ~1 min).

The real check needs ~1 GB of CATS cubes; here four small synthetic runs stand in for
them, laid out exactly as the archive names them (the density of b.1p.01 uncompressed, as
in BUG_LOG.md bug 1).  A "committed" cache and result are made from them first, in a
temporary folder, and the checker is pointed at those instead of the repository's.
"""

import gzip
import io
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_full_rebuild_alexis as fr    # noqa: E402
import data_pipeline_alexis as pl         # noqa: E402
import firstmap_b as fm                   # noqa: E402


def write_fits(path, arr, gz):
    buf = io.BytesIO()
    fits.PrimaryHDU(np.asarray(arr, dtype=np.float32)).writeto(buf)
    data = gzip.compress(buf.getvalue(), mtime=0) if gz else buf.getvalue()
    path.write_bytes(data)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """Synthetic CATS folder + a 'committed' cache, provenance and key result built from it."""
    root = tmp_path_factory.mktemp("fullcheck")
    cats = root / "cats"
    for i, (run, snaps) in enumerate(sorted(pl.CATS_SNAPSHOTS.items())):
        snap = snaps[0]
        rho, B = fm.fake_cube(n=24, seed=10 + i)
        (cats / run).mkdir(parents=True)
        for comp, arr in (("rho", rho), ("bx", B["x"]), ("by", B["y"]), ("bz", B["z"])):
            gz = not (run == "b.1p.01" and comp == "rho")
            name = pl.cats_urls(run, snap, comp)[0 if gz else 1][1]
            write_fits(cats / run / name, arr, gz)
    committed = root / "committed"
    (committed / "data").mkdir(parents=True)
    shutil.copytree(HERE / "data" / "athena", committed / "data" / "athena")
    for run in pl.CATS_SNAPSHOTS:
        shutil.copytree(cats / run, committed / run)
    subprocess.run([sys.executable, str(HERE / "data_pipeline_alexis.py"), "--project",
                    str(committed), "--build", "--no-github", "--source", "both", "--primary",
                    "--log-file", str(committed / "build.jsonl")], cwd=HERE, check=True,
                   capture_output=True)
    subprocess.run([sys.executable, str(HERE / "make_key_result_alexis.py"), "--db",
                    str(committed / fr.CACHE_REL), "--out", str(committed / "results"),
                    "--log-file", str(committed / "kr.jsonl")], cwd=HERE, check=True,
                   capture_output=True)
    return root, cats, committed


@pytest.fixture()
def checker(world, monkeypatch, tmp_path):
    root, cats, committed = world
    cache = tmp_path / "committed_cache.db"
    shutil.copy2(committed / fr.CACHE_REL, cache)
    monkeypatch.setattr(fr, "COMMITTED_CACHE", cache)
    monkeypatch.setattr(fr, "COMMITTED_PROV", committed / "provenance_alexis.json")
    monkeypatch.setattr(fr, "RESULTS_DIR", committed / "results")
    monkeypatch.setattr(fr, "SCRATCH", tmp_path / "scratch")
    monkeypatch.setattr(fr, "REPORT", tmp_path / "report.txt")
    monkeypatch.setattr(fr, "LOG", tmp_path / "check.jsonl")
    fr.lines.clear()
    return cats, cache, tmp_path


def test_rebuild_from_raw_reproduces_committed_cache_and_result(checker):
    cats, _, tmp = checker
    assert fr.main(["--cats-data", str(cats)]) == 0
    report = (tmp / "report.txt").read_text()
    assert "-> IDENTICAL" in report                       # build A == build B
    assert "DIFFERENT" not in report                      # same raw input files
    assert "every committed row reproduced exactly" in report
    assert "VERDICT: REPRODUCED" in report
    # every step is a logged tool call
    log = (tmp / "check.jsonl").read_text()
    for tool in ("fullcheck.build", "fullcheck.compare_rows", "fullcheck.key_result_from_rebuilt"):
        assert f'"tool": "{tool}"' in log


def test_edited_committed_cache_is_reported_and_fails(checker):
    cats, cache, tmp = checker
    con = sqlite3.connect(cache)
    con.execute("UPDATE estimates SET gamma_obs = gamma_obs + 1 WHERE angle = 45")
    con.execute("UPDATE datasets SET source_url = 'https://example.org/old.fits.gz' "
                "WHERE run_id LIKE 'cho:b.1p.01%'")
    con.commit()
    con.close()
    assert fr.main(["--cats-data", str(cats)]) == 1
    report = (tmp / "report.txt").read_text()
    assert "gamma_obs: max |difference| 1.0" in report
    assert "source_url: e.g. cho:b.1p.01" in report and "dens_t750.fits" in report
    assert "VERDICT: the current pipeline" in report and "ADOPT=1" in report


def test_without_cats_cubes_the_check_is_partial(checker):
    _, _, tmp = checker
    assert fr.main([]) == 0
    assert "VERDICT: PARTIAL" in (tmp / "report.txt").read_text()
