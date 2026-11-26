"""
rebuild_alexis.py  --  rebuild every result with the HW10 method, in one command.

The HW10 review changed the method (PHYSICS_REVIEW_alexis.md, section "Changes made"): cubic
sampling of B at every tilt, a KDE on every pixel, tilts in 5 deg steps, three snapshots per
CATS run with bootstrap grades, and the standard Mach numbers.  Every stored number must
therefore be recomputed.  This script does it in order, logging every step as JSON:

  1. pipeline   data_pipeline_alexis.py --fetch --build --verify --all
                (downloads the 8 new CATS snapshots, about 2 GB; reuses files on disk)
  2. arepo      the AREPO runs in runs.json (FITS cubes made by prepare_arepo.py), estimated
                at the same tilts and p_max rules -> cache/arepo_results.csv
  3. turbulence velocity cubes of the primary CATS snapshots (downloaded if absent) and
                measure_turbulence.measure for every run in runs.json -> turbulence.csv
  4. results    stageAB_results.csv = pipeline export + AREPO rows; results.db built from it
                (with --plant-failures if planted_failures.json is wanted for the answer key)
  5. key        hw8/make_ground_truth.py -> hw8/ground_truth_alexis.json,
                hw8/physics_check_alexis.csv
  6. figure     make_key_result_alexis.py -> results/

Usage (in the project folder: runs.json, the CATS run folders and arepo_*/grid256/):
    python rebuild_alexis.py                    # everything
    python rebuild_alexis.py --steps pipeline arepo results figure
    python rebuild_alexis.py --plant-failures   # results.db for the Claude scoring runs

Commit afterwards: cache/, provenance_alexis.json, stageAB_results.csv, turbulence.csv,
runs.json, hw8/ground_truth_alexis.json, hw8/physics_check_alexis.csv, results/.
The first pipeline build after HW10 prints NOT REPRODUCED (the method changed); run
`python data_pipeline_alexis.py --build --verify --all` once more and it must print REPRODUCED.
"""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np                       # noqa: E402

import obs_logging_alexis as obs         # noqa: E402

SEEDS = obs.seed_everything(42)
STEPS = ["pipeline", "arepo", "turbulence", "results", "key", "figure"]


def run(cmd):
    """Run a command in the project folder, echoing it; stop on failure."""
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=HERE, check=True)
    return {"cmd": cmd, "returncode": 0}


@obs.traced("rebuild.pipeline")
def step_pipeline(project):
    return run([sys.executable, "data_pipeline_alexis.py", "--project", str(project),
                "--fetch", "--build", "--verify", "--all"])


def load_runs(project):
    p = project / "runs.json"
    if not p.exists():
        raise SystemExit(f"{p} not found: it lists the runs and their FITS cubes")
    return json.loads(p.read_text())["runs"]


@obs.traced("rebuild.arepo")
def step_arepo(project):
    """Tilted estimates for every AREPO run in runs.json, same angles and rules as the pipeline."""
    import data_pipeline_alexis as pl
    import firstmap_b as fm
    import rotate_maps as rm
    from build_db import RESULT_COLS
    runs = {k: v for k, v in load_runs(project).items()
            if k.startswith("AREPO") and not v.get("planted")}
    out = project / "cache" / "arepo_results.csv"
    rows = []
    for run_id, e in sorted(runs.items()):
        files = [project / e[k] for k in ("rho", "bx", "by", "bz")]
        if not all(f.exists() for f in files):
            print(f"[arepo] {run_id}: cubes not found, skipped (run prepare_arepo.py first)")
            continue
        rho, B = obs.call("firstmap_b.load_cubes", fm.load_cubes, *map(str, files))
        fa, pa, mean = rm.choose_axes(B)
        for ang in pl.ANGLES:
            _, Q, U, p, c2 = obs.call("rotate_maps.rotated_maps", rm.rotated_maps,
                                      rho, B, ang, fa, pa)
            truth = rm.true_gamma(mean, ang, fa, pa)
            for rule, pct in pl.PMAX_RULES.items():
                r = obs.call(f"firstmap_b.estimate_from_maps[{rule}]", fm.estimate_from_maps,
                             Q, U, p, c2, truth, mask_border=True, pmax_percentile=pct)
                rows.append({"run": run_id + ("_pmax100" if rule == "max" else ""),
                             "angle": ang, "field_axis": fa,
                             **{k: round(float(r[k]), 6) for k in RESULT_COLS}})
        print(f"[arepo] {run_id}: {len(pl.ANGLES)} tilts x 2 rules")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["run", "angle", "field_axis"] + RESULT_COLS,
                           lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["run"], r["angle"])))
    return {"csv": out, "n_rows": len(rows), "runs": sorted(runs)}


@obs.traced("rebuild.turbulence")
def step_turbulence(project):
    """turbulence.csv for every run in runs.json, with the standard Mach numbers.  CATS
    velocity cubes of the run's snapshot are downloaded next to its other cubes if absent."""
    import data_pipeline_alexis as pl
    import firstmap_b as fm
    import measure_turbulence as mt
    from astropy.io import fits
    out = project / "turbulence.csv"
    rows = []
    for run_id, e in sorted(load_runs(project).items()):
        files = [project / e[k] for k in ("rho", "bx", "by", "bz")]
        if e.get("planted") or not all(f.exists() for f in files):
            continue
        rho, B = fm.load_cubes(*map(str, files))
        v = None
        if run_id in pl.CATS_RUNS:
            folder, snap = pl.CATS_RUNS[run_id]
            v = {}
            for c in "xyz":
                name = f"vel{c}_t{snap}.fits.gz"
                dest = project / folder / name
                if not dest.exists():
                    url = f"{pl.CATS_BASE_URL.rstrip('/')}/MHD/256/{folder}/t_{snap}/{name}"
                    obs.call("data_pipeline.download", pl.download, url, dest, project)
                v[c] = np.asarray(fits.getdata(dest), dtype=np.float64)
        cs = mt.sound_speed_from_name(run_id)
        r = obs.call("measure_turbulence.measure", mt.measure, rho, B, v=v, cs=cs)
        rows.append({"run": run_id, **r})
        print(f"[turbulence] {run_id}: dB/B0 = {r['dB_over_B0']:.3f}, M_s = {r['M_s']:.2f}, "
              f"M_A = {r['M_A']:.2f}")
    keys = list(rows[0]) if rows else ["run"]
    for r in rows:
        keys += [k for k in r if k not in keys]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return {"csv": out, "n_runs": len(rows)}


@obs.traced("rebuild.results")
def step_results(project, plant=False):
    """stageAB_results.csv (pipeline export + AREPO rows) and results.db."""
    import build_db
    parts = [project / "cache" / "results_rotated.csv", project / "cache" / "arepo_results.csv"]
    out = project / "stageAB_results.csv"
    header, rows = None, []
    for p in parts:
        if not p.exists():
            print(f"[results] {p.name} not found, skipped")
            continue
        with open(p, newline="") as f:
            r = csv.reader(f)
            h = next(r)
            header = header or h
            if h != header:
                raise SystemExit(f"column mismatch in {p}")
            rows += list(r)
    with open(out, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(sorted(rows, key=lambda r: (r[0], float(r[1]))))
    import os
    cwd = os.getcwd()
    os.chdir(project)                       # build_db writes planted_failures.json in the cwd
    try:
        build_db.build(str(out), str(project / "turbulence.csv"), str(project / "results.db"),
                       plant=plant)
    finally:
        os.chdir(cwd)
    import evaluate
    g = evaluate.grade_csv(str(out))
    g.to_csv(project / "stageAB_grades.csv", index=False)
    return {"results_csv": out, "n_rows": len(rows), "planted": plant}


@obs.traced("rebuild.answer_key")
def step_key(project, allow_skipped=False):
    """Regenerate the HW8 answer key into temporary files, and replace the committed ones only
    if no run was skipped: make_ground_truth.py drops the items of any run whose cubes are
    missing, which would silently shrink the 20-item key."""
    import os
    tmp_key, tmp_pc = project / "ground_truth_new.json", project / "physics_check_new.csv"
    run([sys.executable, "hw8/make_ground_truth.py", "--project", str(project),
         "--out", str(tmp_key), "--physics-out", str(tmp_pc)])
    new = json.loads(tmp_key.read_text())
    if new["_skipped"] and not allow_skipped:
        print(f"[key] NOT replacing hw8/ground_truth_alexis.json: runs skipped (cubes missing): "
              f"{new['_skipped']}.  New key left in {tmp_key.name}; use --allow-skipped to accept it.")
        return {"replaced": False, "skipped": new["_skipped"], "n_items": len(new["items"])}
    os.replace(tmp_key, HERE / "hw8" / "ground_truth_alexis.json")
    os.replace(tmp_pc, HERE / "hw8" / "physics_check_alexis.csv")
    return {"replaced": True, "n_items": len(new["items"])}


@obs.traced("rebuild.figure")
def step_figure(project):
    return run([sys.executable, "make_key_result_alexis.py",
                "--db", str(project / "cache" / "pipeline_cache_alexis.db"),
                "--out", str(HERE / "results")])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", default=str(HERE))
    ap.add_argument("--steps", nargs="+", choices=STEPS, default=STEPS)
    ap.add_argument("--plant-failures", action="store_true",
                    help="build results.db with the planted bad number (for the answer key)")
    ap.add_argument("--allow-skipped", action="store_true",
                    help="replace the answer key even if some runs' cubes were missing")
    ap.add_argument("--log-file", default=None)
    a = ap.parse_args(argv)
    project = Path(a.project).resolve()
    obs.configure("rebuild", log_file=a.log_file, project_root=project)
    for s in STEPS:
        if s not in a.steps:
            continue
        print(f"\n===== {s}", flush=True)
        if s == "pipeline":
            step_pipeline(project)
        elif s == "arepo":
            step_arepo(project)
        elif s == "turbulence":
            step_turbulence(project)
        elif s == "results":
            step_results(project, plant=a.plant_failures)
        elif s == "key":
            step_key(project, allow_skipped=a.allow_skipped)
        elif s == "figure":
            step_figure(project)
    print("\ndone. Now run: python data_pipeline_alexis.py --build --verify --all  "
          "(must print REPRODUCED), then commit the files listed in rebuild_alexis.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
