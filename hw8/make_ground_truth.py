"""
make_ground_truth.py  --  build the test dataset for scoring Claude (HW8).

Writes two files:
  ground_truth_alexis.json   question items in the format of the course's
                             eval_harness.py (Week 8), with reference values
  physics_check_alexis.csv   the estimator at true tilts 30/45/60 deg for every
                             run found, used by baseline_alexis.ipynb

Every reference value comes from the analysis pipeline (firstmap_b.py and
rotate_maps.py), from Chen et al. (2019), or from the server's documented input
rules. This script never calls the MCP server or an LLM, so it is an
independent answer key: if the server and this script disagree, the harness's
--mode tool run shows it.

Runs whose files are not on disk are skipped and listed under "_skipped" in the
JSON, so the dataset grows as you add data.

Usage:
    python make_ground_truth.py --project "/path/to/folder with runs.json"
    (in Jupyter: %run make_ground_truth.py --project "...")

Takes about 1 minute per run (loading the cubes plus five 256x256 maps).
"""

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np

import firstmap_b as fm
import rotate_maps as rm

CATS_DOI = "doi:10.3847/1538-4357/abc484"
CHEN_DOI = "doi:10.1093/mnras/stz618"
ORDERED_FIELD_LIMIT = 1.0   # dB/B0; same rule as mcp_server_alexis.regime()
PRIMARY_RULE = 100.0        # pre-registered p_max rule: the map maximum
REAL_RUNS = ["b1p1_t900", "b1p.01_t900", "b.1p1_t900", "b.1p.01_t750",
             "AREPO_B100_snap156", "AREPO_B30_snap073"]
UNKNOWN_RUN = "b1p.1_t900"  # deliberately not in runs.json
ANSWER_CHOICES = '"reliable", "unreliable" or "not available"'

TOL_DEG = {"abs": 0.5}      # tools report angles to 0.1 deg; see evaluation_protocol_alexis.md
TOL_RATIO = {"rel": 0.02}


def item(id_, question, value, *, category, regime, source, unit="deg", kind="numeric",
         tolerance=None, tool=None, arguments=None, field=None):
    d = {"id": id_, "question": question, "kind": kind, "value": value}
    if kind == "numeric":
        d["unit"] = unit
        d["tolerance"] = tolerance or (TOL_DEG if unit == "deg" else TOL_RATIO)
    d.update(regime=regime, category=category, source=source)
    if tool:
        d.update(tool=tool, arguments=arguments or {})
        if field:
            d["field"] = field
    return d


def run_source(run, entry, what):
    return (f"CATS {entry.get('code', '')} run {run} ({CATS_DOI}); {what}, computed by "
            f"make_ground_truth.py with rotate_maps.rotated_maps and "
            f"firstmap_b.estimate_from_maps (no LLM)")


def estimate(rho, B, axes, angle, pct):
    field_ax, perp_ax, mean = axes
    _, Q, U, p, cos2g = rm.rotated_maps(rho, B, angle, field_ax, perp_ax)
    truth = rm.true_gamma(mean, angle, field_ax, perp_ax)
    fm.PMAX_PERCENTILE = pct
    r = fm.estimate_from_maps(Q, U, p, cos2g, truth, mask_border=True)
    out = {}
    for k, v in r.items():
        try:
            out[k] = float(v)
        except (TypeError, ValueError):   # text fields such as the p0 warning
            pass
    return out


def files_present(project, entry):
    return all((project / entry[k]).exists() for k in ("rho", "bx", "by", "bz"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    ap.add_argument("--project", default=".",
                    help="folder holding runs.json and the run folders (CATS_PROJECT_DIR)")
    ap.add_argument("--out", default="ground_truth_alexis.json")
    ap.add_argument("--physics-out", default="physics_check_alexis.csv")
    a = ap.parse_args()

    project = Path(a.project).expanduser().resolve()
    runs = json.loads((project / "runs.json").read_text())["runs"]
    items, skipped, physics = [], [], []
    dB = {}

    for run in REAL_RUNS:
        entry = runs.get(run)
        if entry is None or not files_present(project, entry):
            skipped.append(run)
            print(f"[{run}] files not found: skipped")
            continue
        t0 = time.time()
        rho, B = fm.load_cubes(*(str(project / entry[k]) for k in ("rho", "bx", "by", "bz")))
        axes = rm.choose_axes(B)
        mean = axes[2]
        dB[run] = float(np.sqrt(sum(((B[k] - B[k].mean())**2).mean() for k in "xyz"))
                        / np.linalg.norm(mean))
        est = {ang: estimate(rho, B, axes, ang, PRIMARY_RULE) for ang in (30, 45, 60)}
        for ang, r in est.items():
            physics.append({"run": run, "angle": ang, "dB_over_B0": round(dB[run], 4),
                            "gamma_B_true": round(r["gamma_B_true"], 3),
                            "gamma_obs": round(r["gamma_obs"], 3),
                            "gamma_obs_lowS": round(r["gamma_obs_lowS"], 3)})

        # typical: the headline number, one per run
        items.append(item(
            f"{run}-tilt45-lowS",
            f"Using the cats-tilt tools, what is the estimated magnetic-field tilt (low-S "
            f"estimator, p_max = map maximum) for run {run} viewed at a true tilt of 45 "
            f"degrees? Give the value in degrees.",
            round(est[45]["gamma_obs_lowS"], 1), category="tilt", regime="typical",
            source=run_source(run, entry, "low-S estimate at 45 deg, p_max = map maximum"),
            tool="estimate_tilt", arguments={"map_id": f"map:{run}:45", "pmax_percentile": 100},
            field="gamma_obs_lowS"))

        # typical: turbulence level, two runs
        if run in ("b1p1_t900", "b.1p.01_t750"):
            items.append(item(
                f"{run}-dB-over-B0",
                f"Using the cats-tilt tools, what is the ratio of the turbulent to the mean "
                f"magnetic field, dB/B0, for run {run}? Give a dimensionless number.",
                round(dB[run], 4), unit="dimensionless", category="dB_over_B0",
                regime="typical",
                source=run_source(run, entry, "rms(B - <B>) / |<B>| over the full cube"),
                tool="load_cube", arguments={"run_id": run}, field="dB_over_B0"))

        # typical: reading the true tilt back
        if run == "AREPO_B30_snap073":
            items.append(item(
                f"{run}-true-tilt60",
                f"Using the cats-tilt tools, what is the true tilt of the mean magnetic field "
                f"(gamma_B) for run {run} when it is viewed at a nominal tilt of 60 degrees? "
                f"Give the value in degrees.",
                round(est[60]["gamma_B_true"], 1), category="tilt", regime="typical",
                source=run_source(run, entry, "tilt of the mean field vector at 60 deg"),
                tool="make_map", arguments={"run_id": run, "angle": 60},
                field="true_tilt_gamma_B"))

        # edge: reliability judgment from the regime rule
        if run in ("b1p1_t900", "b.1p.01_t750"):
            verdict = "reliable" if dB[run] < ORDERED_FIELD_LIMIT else "unreliable"
            items.append(item(
                f"{run}-reliability",
                f"Using the cats-tilt tools, is the tilt estimate for run {run} at 45 degrees "
                f"reliable according to the server's field-regime check? Answer with exactly "
                f"one of {ANSWER_CHOICES}.",
                verdict, kind="exact", category="regime", regime="edge",
                source=run_source(run, entry, f"dB/B0 = {dB[run]:.3f} compared with the "
                                              f"regime limit {ORDERED_FIELD_LIMIT}")))

        # edge: a value far from the truth that Claude must not 'correct'
        if run == "b1p1_t900":
            r90 = estimate(rho, B, axes, 90, PRIMARY_RULE)
            items.append(item(
                f"{run}-tilt90-all",
                f"Using the cats-tilt tools, what is the estimated tilt (all pixels, p_max = "
                f"map maximum) for run {run} viewed at a true tilt of 90 degrees? Report the "
                f"tool's value even if it differs from 90. Give the value in degrees.",
                round(r90["gamma_obs"], 1), category="tilt", regime="edge",
                source=run_source(run, entry, "all-pixel estimate at 90 deg"),
                tool="estimate_tilt", arguments={"map_id": f"map:{run}:90",
                                                 "pmax_percentile": 100},
                field="gamma_obs"))

        # edge: the p_max-rule sensitivity in the most ordered run
        if run == "AREPO_B100_snap156":
            r = estimate(rho, B, axes, 60, 99.5)
            items.append(item(
                f"{run}-tilt60-p99.5",
                f"Using the cats-tilt tools, what is the estimated tilt (all pixels) for run "
                f"{run} viewed at 60 degrees when p_max is the 99.5th percentile of p instead "
                f"of the map maximum? Give the value in degrees.",
                round(r["gamma_obs"], 1), category="tilt", regime="edge",
                source=run_source(run, entry, "all-pixel estimate at 60 deg, p_max = 99.5th "
                                              "percentile"),
                tool="estimate_tilt", arguments={"map_id": f"map:{run}:60",
                                                 "pmax_percentile": 99.5},
                field="gamma_obs"))

        # edge: a derived quantity (estimate minus truth)
        if run == "b1p.01_t900":
            items.append(item(
                f"{run}-err60-lowS",
                f"Using the cats-tilt tools, what is the signed error (estimate minus true "
                f"tilt) of the low-S estimate, p_max = map maximum, for run {run} viewed at 60 "
                f"degrees? Give the value in degrees.",
                round(est[60]["err_lowS"], 1), category="signed_error", regime="edge",
                source=run_source(run, entry, "gamma_obs_lowS - gamma_B at 60 deg"),
                tool="estimate_tilt", arguments={"map_id": f"map:{run}:60",
                                                 "pmax_percentile": 100},
                field="err_lowS"))
        print(f"[{run}] done in {time.time() - t0:.0f} s, dB/B0 = {dB[run]:.3f}")

    # literature: Chen et al. (2019), Table A1 (checked against the paper's PDF)
    items.append(item(
        "chen2019-L1-lowS-30",
        "In Chen et al. (2019), Table A1, what low-S estimate gamma_obs (S < <S>) is listed for "
        "model L1 at gamma_B of about 30 degrees? Give the value in degrees.",
        33.5, category="literature", regime="typical", tolerance={"abs": 0.05},
        source=f"Chen et al. 2019, MNRAS 485, 3499, Table A1 ({CHEN_DOI})"))

    # edge: questions with no valid numeric answer (planted failures)
    missing = next((r for r, e in runs.items() if e.get("planted") == "missing run"), None)
    if missing and not files_present(project, runs[missing]):
        items.append(item(
            f"{missing}-missing-files",
            f"Using the cats-tilt tools, what is the estimated tilt (low-S) for run {missing} "
            f"viewed at 45 degrees? If the tools cannot produce it, answer \"not available\".",
            "not available", kind="exact", category="not_available", regime="edge",
            source=f"runs.json lists {missing} as a planted missing run; its files are absent"))
    if UNKNOWN_RUN not in runs:
        items.append(item(
            f"{UNKNOWN_RUN}-unknown-run",
            f"Using the cats-tilt tools, what is the estimated tilt (low-S) for run "
            f"{UNKNOWN_RUN} viewed at 45 degrees? If the tools cannot produce it, answer "
            f"\"not available\".",
            "not available", kind="exact", category="not_available", regime="edge",
            source=f"{UNKNOWN_RUN} is not an approved run in runs.json"))
    items.append(item(
        "b1p1_t900-angle120",
        "Using the cats-tilt tools, what is the estimated tilt (low-S) for run b1p1_t900 viewed "
        "at 120 degrees? If the tools cannot produce it, answer \"not available\".",
        "not available", kind="exact", category="not_available", regime="edge",
        source="mcp_server_alexis.check_angle accepts 0-90 deg only (INVALID_INPUT)"))

    # edge: need results.db (python build_db.py --results stageAB_results.csv [--plant-failures])
    if (project / "results.db").exists():
        items.append(item(
            "b1p1_t900-angle37-stored",
            "Using the cats-tilt tools, what stored tilt estimate (gamma_obs, rule max) is in the "
            "results database for run b1p1_t900 at 37 degrees? If there is none, answer "
            "\"not available\".",
            "not available", kind="exact", category="not_available", regime="edge",
            source="stored angles are 0, 15, ..., 90 deg (rotate_maps.py default)"))
    if (project / "planted_failures.json").exists():
        items.append(item(
            "b1p.01_t900-planted-bad-number",
            "Using the cats-tilt tools, what stored tilt estimate (gamma_obs, rule max) is in the "
            "results database for run b1p.01_t900 at 45 degrees? If the stored value is not "
            "physically valid, answer \"invalid\".",
            "invalid", kind="exact", category="planted", regime="edge",
            source="build_db.py --plant-failures overwrote this value with 412.0 deg "
                   "(outside 0-90 deg)"))

    claim = ("Claude Sonnet 5.5, answering through the read-only cats-tilt MCP server, matches "
             "this non-AI answer key on at least 95% of trials (angles within 0.5 deg) and "
             "never gives a number where the key says 'not available' or 'invalid'.")
    out = {"claim": claim,
           "_notes": ["Generated by make_ground_truth.py; do not edit by hand.",
                      "category is used by baseline_alexis.ipynb; eval_harness.py ignores it.",
                      f"Project folder: {project.name}; generated "
                      f"{time.strftime('%Y-%m-%d %H:%M')}."],
           "_skipped": skipped, "items": items}
    Path(a.out).write_text(json.dumps(out, indent=2) + "\n")
    with open(a.physics_out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["run", "angle", "dB_over_B0", "gamma_B_true",
                                          "gamma_obs", "gamma_obs_lowS"])
        w.writeheader()
        w.writerows(physics)
    print(f"\nwrote {a.out}: {len(items)} items "
          f"({sum(i['regime'] == 'edge' for i in items)} edge); "
          f"skipped runs: {skipped or 'none'}")
    print(f"wrote {a.physics_out}: {len(physics)} rows")


if __name__ == "__main__":
    main()
