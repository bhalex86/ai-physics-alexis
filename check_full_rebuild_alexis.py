"""check_full_rebuild_alexis.py  --  rebuild the cache from the raw simulation files, twice,
and compare it with the committed one (PHYS 7440, HW10).

`make check-reproducible` re-runs the *analysis* on the committed cache.  This script checks
the step before it: that the current pipeline, started from the raw files (Athena++ HDF5,
CATS FITS cubes), produces the cache that the committed result was computed from.

    python check_full_rebuild_alexis.py                         # Athena++ only (no download)
    python check_full_rebuild_alexis.py --cats-data ~/cats      # + CATS cubes already on disk
    python check_full_rebuild_alexis.py --fetch                 # + download CATS (~1 GB)
    python check_full_rebuild_alexis.py --cats-data ~/cats --adopt   # and replace the
                                                                # committed cache + result

Steps (each one is a logged tool call; the log goes to results/full_rebuild_check_alexis.jsonl):
  1. build A: a scratch project with only the raw files, `data_pipeline_alexis.py --build`
  2. build B: a second, independent scratch project from the same raw files
  3. A vs B: canonical cache hashes must be equal (the pipeline is deterministic from raw)
  4. raw files: every SHA-256 must equal the one stored in the committed cache (same input)
  5. A vs committed cache: every row of every run, column by column
  6. if every committed run was rebuilt: the key result from cache A vs results/

--adopt (only when 1-6 ran on every committed run and 3-4 passed): copies cache A and its
provenance into the repository and rewrites results/ with `make result`.  Then commit.

A report is written to results/full_rebuild_check_alexis.txt.  Exit code 0 means: A == B,
same raw input, and (when complete) the committed cache was reproduced or has been adopted.
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import obs_logging_alexis as obs            # noqa: E402

SEEDS = obs.seed_everything(42)              # nothing here is random; set anyway

import data_pipeline_alexis as pl            # noqa: E402

CACHE_REL = Path("cache") / "pipeline_cache_alexis.db"
COMMITTED_CACHE = HERE / CACHE_REL
COMMITTED_PROV = HERE / "provenance_alexis.json"
SCRATCH = HERE / ".fullcheck"
RESULTS_DIR = HERE / "results"             # the committed key result
REPORT = HERE / "results" / "full_rebuild_check_alexis.txt"
LOG = HERE / "results" / "full_rebuild_check_alexis.jsonl"
KEYS = {"datasets": ("run_id",), "estimates": ("run_id", "angle", "rule")}
TOL = 1e-9                                   # the cache already rounds to 6 decimals

lines = []


def say(msg=""):
    print(msg)
    lines.append(msg)


# ------------------------------------------------------------------ the scratch projects
@obs.traced("fullcheck.make_scratch")
def make_scratch(name, cats_dirs):
    """A project folder holding only raw inputs: data/athena/ (copied) and the CATS run
    folders (each file symlinked, so 1 GB is not copied).  No cache, no provenance."""
    proj = SCRATCH / name
    shutil.rmtree(proj, ignore_errors=True)
    proj.mkdir(parents=True)
    shutil.copytree(HERE / "data" / "athena", proj / "data" / "athena")
    linked = 0
    for run, src in sorted(cats_dirs.items()):
        (proj / run).mkdir()
        for f in sorted(Path(src).iterdir()):
            if f.is_file() and ".fits" in f.name:
                (proj / run / f.name).symlink_to(f.resolve())
                linked += 1
    return {"project": str(proj), "cats_files_linked": linked}


@obs.traced("fullcheck.build")
def build(proj, source, run_args, fetch):
    """Run the pipeline in its own process, as a user would."""
    cmd = [sys.executable, str(HERE / "data_pipeline_alexis.py"), "--project", str(proj),
           "--build", "--no-github", "--source", source,
           "--log-file", str(Path(proj) / "logs" / "pipeline_alexis.jsonl")] + run_args
    if fetch:
        cmd.insert(3, "--fetch")
    env = dict(os.environ, PYTHONHASHSEED="0", OBS_PARENT_RUN_ID=obs._state.get("run_id") or "")
    p = subprocess.run(cmd, cwd=HERE, env=env, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"pipeline failed ({p.returncode}):\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}")
    return {"returncode": 0, "canonical_hash": pl.canonical_hash(Path(proj) / CACHE_REL)}


# ------------------------------------------------------------------ comparisons
def rows(db, table):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out = {tuple(r[k] for k in KEYS[table]): dict(r) for r in con.execute(f"SELECT * FROM {table}")}
    con.close()
    return out


def run_ids(db):
    return sorted(k[0] for k in rows(db, "datasets"))


@obs.traced("fullcheck.compare_raw_inputs")
def compare_raw_inputs(rebuilt_db):
    """The SHA-256 of every raw cube used now vs the one stored in the committed cache."""
    old, new = rows(COMMITTED_CACHE, "datasets"), rows(rebuilt_db, "datasets")
    out = {}
    for key in sorted(set(old) & set(new)):
        cols = [c for c in old[key] if c.startswith("sha256_") and old[key][c]]
        out[key[0]] = all(old[key][c] == new[key].get(c) for c in cols)
    return out


@obs.traced("fullcheck.compare_rows")
def compare_rows(rebuilt_db, only_runs):
    """Column-by-column comparison, restricted to the runs that were rebuilt."""
    report = {}
    for table in KEYS:
        old = {k: v for k, v in rows(COMMITTED_CACHE, table).items() if k[0] in only_runs}
        new = {k: v for k, v in rows(rebuilt_db, table).items() if k[0] in only_runs}
        diffs, examples = {}, {}
        for key in sorted(set(old) & set(new)):
            for col, a in old[key].items():
                b = new[key].get(col)
                if a == b:
                    continue
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    d = abs(a - b)
                    if d <= TOL:
                        continue
                    diffs[col] = max(diffs.get(col, 0.0), round(d, 6))
                else:
                    diffs[col] = "text differs"
                    examples.setdefault(col, (key, a, b))
        report[table] = {"common_rows": len(set(old) & set(new)),
                         "only_committed": len(set(old) - set(new)),
                         "only_rebuilt": len(set(new) - set(old)),
                         "columns_differing": diffs, "text_examples": examples}
    return report


@obs.traced("fullcheck.key_result_from_rebuilt")
def key_result_from(db):
    out = SCRATCH / "key_result"
    shutil.rmtree(out, ignore_errors=True)
    subprocess.run([sys.executable, str(HERE / "make_key_result_alexis.py"), "--db", str(db),
                    "--out", str(out), "--log-file", str(SCRATCH / "key_result.jsonl")],
                   cwd=HERE, check=True, capture_output=True, text=True)
    p = subprocess.run([sys.executable, str(HERE / "make_key_result_alexis.py"), "--compare",
                        str(out), str(RESULTS_DIR)], cwd=HERE, capture_output=True, text=True)
    return {"identical": p.returncode == 0, "comparison": p.stdout.strip()}


@obs.traced("fullcheck.adopt")
def adopt(proj):
    """Replace the committed cache and provenance with build A, then remake results/."""
    shutil.copy2(Path(proj) / CACHE_REL, COMMITTED_CACHE)
    rr = Path(proj) / "cache" / "results_rotated.csv"
    if rr.exists():
        shutil.copy2(rr, HERE / "cache" / "results_rotated.csv")
    prov = json.loads((Path(proj) / "provenance_alexis.json").read_text())
    COMMITTED_PROV.write_text(json.dumps(prov, indent=1, sort_keys=True) + "\n")
    subprocess.run(["make", "result"], cwd=HERE, check=True)
    return {"cache": str(CACHE_REL), "canonical_hash": pl.canonical_hash(COMMITTED_CACHE)}


# ------------------------------------------------------------------ main
def find_cats_dirs(cats_data):
    if not cats_data:
        return {}
    base = Path(cats_data).expanduser()
    return {run: base / run for run in pl.CATS_SNAPSHOTS if (base / run).is_dir()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cats-data", help="folder holding the CATS run folders (b1p1/, b.1p.01/, ...)")
    ap.add_argument("--fetch", action="store_true", help="download the CATS cubes (~1 GB)")
    ap.add_argument("--runs", choices=["primary", "all"], default="primary",
                    help="CATS snapshots: the committed result uses 'primary' (default)")
    ap.add_argument("--adopt", action="store_true",
                    help="replace the committed cache and result with the rebuild, if complete")
    a = ap.parse_args(argv)

    LOG.parent.mkdir(exist_ok=True)
    LOG.unlink(missing_ok=True)
    obs.configure("full_rebuild_check", log_file=LOG, project_root=HERE)
    cats_dirs = find_cats_dirs(a.cats_data)
    use_cats = bool(cats_dirs) or a.fetch
    source = "both" if use_cats else "athena"
    run_args = (["--all"] if a.runs == "all" else ["--primary"]) if use_cats else []

    say("Full rebuild check: raw simulation files -> cache, built twice, vs the committed cache")
    say(f"pipeline version {pl.PIPELINE_VERSION}; sources: {source}; CATS run folders found: "
        f"{', '.join(sorted(cats_dirs)) or 'none'}{' (+ download)' if a.fetch else ''}")

    make_scratch("A", cats_dirs)
    ra = build(SCRATCH / "A", source, run_args, a.fetch)
    # B is independent of A's cache, but reuses A's downloaded raw files (no second download)
    dirs_b = cats_dirs or {r: SCRATCH / "A" / r for r in pl.CATS_SNAPSHOTS if (SCRATCH / "A" / r).is_dir()}
    make_scratch("B", dirs_b if use_cats else {})
    rb = build(SCRATCH / "B", source, run_args, False)
    db_a = SCRATCH / "A" / CACHE_REL

    say("")
    say("1. two independent builds from the raw files")
    say(f"   build A  {ra['canonical_hash']}")
    say(f"   build B  {rb['canonical_hash']}")
    deterministic = ra["canonical_hash"] == rb["canonical_hash"]
    say(f"   -> {'IDENTICAL' if deterministic else 'DIFFERENT'}")

    committed_runs, rebuilt_runs = run_ids(COMMITTED_CACHE), run_ids(db_a)
    common = sorted(set(committed_runs) & set(rebuilt_runs))
    complete = set(committed_runs) <= set(rebuilt_runs)
    say("")
    say(f"2. runs in the committed cache: {len(committed_runs)}; rebuilt: {len(common)} of them"
        + ("" if complete else f"; NOT rebuilt here: {', '.join(sorted(set(committed_runs) - set(rebuilt_runs)))}"))

    raw = compare_raw_inputs(db_a)
    same_input = all(raw.values())
    say("")
    say("3. raw input files (SHA-256 vs the hashes stored in the committed cache)")
    for run, ok in raw.items():
        say(f"   {run:<36} {'SAME' if ok else 'DIFFERENT'}")

    cmp = compare_rows(db_a, set(common))
    identical_rows = all(not t["columns_differing"] and not t["only_committed"] for t in cmp.values())
    say("")
    say("4. rebuilt rows vs committed rows (runs rebuilt above)")
    for table, t in cmp.items():
        say(f"   {table}: {t['common_rows']} rows in both, {t['only_committed']} only committed, "
            f"{t['only_rebuilt']} only rebuilt (new tilts/snapshots of the HW10 method)")
        for col, d in sorted(t["columns_differing"].items()):
            if d == "text differs":
                key, old, new = t["text_examples"][col]
                say(f"      {col}: e.g. {key[0]}: {old} -> {new}")
            else:
                say(f"      {col}: max |difference| {d}")
    if identical_rows:
        say("   -> every committed row reproduced exactly")

    result = None
    if complete:
        result = key_result_from(db_a)
        say("")
        say("5. key result computed from the rebuilt cache vs results/")
        say("   " + result["comparison"].replace("\n", "\n   "))

    say("")
    old_version = (json.loads(COMMITTED_PROV.read_text()).get("pipeline", {}).get("version")
                   if COMMITTED_PROV.exists() else None)
    if not complete:
        verdict = "PARTIAL" if deterministic and same_input else "FAILED"
        say(f"VERDICT: {verdict}. The runs rebuilt here are "
            + ("deterministic from their raw files" if deterministic else "NOT deterministic")
            + (", and reproduce the committed rows exactly." if identical_rows else
               f", but differ from the committed rows (above): the committed cache was built by "
               f"pipeline {old_version}, this is {pl.PIPELINE_VERSION}.")
            + " The CATS runs need their cubes: `make check-full-rebuild CATS_DATA=<folder>` "
              "(or FETCH=1); add ADOPT=1 to replace the committed cache with the rebuild.")
    elif identical_rows and result["identical"]:
        verdict = "REPRODUCED"
        say("VERDICT: REPRODUCED. The committed cache and result follow from the raw files "
            "with the current pipeline.")
    else:
        verdict = "DIFFERS FROM COMMITTED"
        say("VERDICT: the current pipeline, run from the raw files, does not give the committed "
            "cache (differences above). " + ("Adopting the rebuild." if a.adopt else
            "Run again with ADOPT=1 to replace the committed cache and result, then commit."))

    ok = deterministic and same_input
    if a.adopt and complete and ok and verdict != "REPRODUCED":
        info = adopt(SCRATCH / "A")
        say(f"ADOPTED: cache/ and provenance_alexis.json replaced (hash {info['canonical_hash']}); "
            "results/ remade. Check the README numbers, then commit.")
    elif a.adopt and not (complete and ok):
        say("NOT ADOPTED: the rebuild is incomplete or not deterministic.")

    REPORT.write_text("\n".join(lines) + "\n")
    print(f"\nreport: {REPORT}   log: {LOG}")
    obs.shutdown()
    return 0 if ok and (not complete or verdict in ("REPRODUCED",) or a.adopt) else 1


if __name__ == "__main__":
    sys.exit(main())
