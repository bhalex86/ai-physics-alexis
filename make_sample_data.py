"""
make_sample_data.py  --  build a small, self-contained sample project so the MCP server
can be installed and run from scratch, with no downloads.

Writes sample_project/ next to this file (about 5 MB, under a minute):
  sample_run/dens.fits, magx.fits, magy.fits, magz.fits
                      a 64^3 synthetic turbulent cube, mean field along x
  sample_stokes.fits  2-D Stokes I, Q, U maps of that cube (HDUs 0, 1, 2)
  runs.json           registers "sample_run" as an approved run
  results_rotated.csv the estimator at 7 tilts x 2 p_max rules (from rotate_maps.py code)
  turbulence.csv      the cube's dB/B0 and density spread
  results.db          built from the two CSVs by build_db.py

Then point the server at it:
    CATS_PROJECT_DIR=sample_project python mcp_server_alexis.py

The cube is synthetic (fixed random seed), not a CATS simulation: it exercises every
tool, but its numbers are not physics results. Real CATS data: see README.md.
"""

import csv
import json
import os
import sys
from pathlib import Path

import numpy as np
from astropy.io import fits

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_db                  # noqa: E402
import firstmap_b as fm          # noqa: E402
import rotate_maps as rm         # noqa: E402

ANGLES = [0, 15, 30, 45, 60, 75, 90]
RULES = {"max": 100.0, "p99.5": 99.5}


def main(out_dir=HERE / "sample_project", n=64):
    out_dir = Path(out_dir)
    run_dir = out_dir / "sample_run"
    run_dir.mkdir(parents=True, exist_ok=True)

    rho, B = fm.fake_cube(n=n, seed=0)
    for name, arr in [("dens", rho), ("magx", B["x"]), ("magy", B["y"]), ("magz", B["z"])]:
        fits.PrimaryHDU(arr.astype(np.float32)).writeto(run_dir / f"{name}.fits", overwrite=True)

    _, Q, U, _, _ = fm.stokes_maps(rho, B, los_axis=0)
    fits.HDUList([fits.PrimaryHDU(rho.sum(axis=0).astype(np.float32)),
                  fits.ImageHDU((fm.P0_TRUE * Q).astype(np.float32)),
                  fits.ImageHDU((fm.P0_TRUE * U).astype(np.float32))]
                 ).writeto(out_dir / "sample_stokes.fits", overwrite=True)

    with open(out_dir / "runs.json", "w") as f:
        json.dump({"note": "Sample project made by make_sample_data.py (synthetic cube).",
                   "runs": {"sample_run": {
                       "code": "synthetic", "gravity": False, "snapshot": "seed 0",
                       "description": f"synthetic {n}^3 turbulent cube, mean field along x "
                                      "(not a CATS simulation)",
                       "rho": "sample_run/dens.fits", "bx": "sample_run/magx.fits",
                       "by": "sample_run/magy.fits", "bz": "sample_run/magz.fits"}}}, f, indent=2)

    field_ax, perp_ax, mean = rm.choose_axes(B)
    cols = ["run", "angle", "field_axis"] + build_db.RESULT_COLS
    with open(out_dir / "results_rotated.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for rule, pct in RULES.items():
            fm.PMAX_PERCENTILE = pct
            name = "sample_run" + ("_pmax100" if rule == "max" else "")
            for th in ANGLES:
                _, Qr, Ur, p, cos2 = rm.rotated_maps(rho, B, th, field_ax, perp_ax)
                truth = rm.true_gamma(mean, th, field_ax, perp_ax)
                r = fm.estimate_from_maps(Qr, Ur, p, cos2, truth, mask_border=True)
                w.writerow({"run": name, "angle": th, "field_axis": field_ax,
                            **{k: r[k] for k in build_db.RESULT_COLS}})
    fm.PMAX_PERCENTILE = 99.5

    dB = float(np.sqrt(sum(((B[k] - B[k].mean())**2).mean() for k in "xyz")))
    with open(out_dir / "turbulence.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run", "field_axis", "dB_over_B0", "rho_sigma_over_mean",
                    "rho_max_over_mean", "M_s", "M_A"])
        w.writerow(["sample_run", field_ax, dB / float(np.linalg.norm(mean)),
                    rho.std() / rho.mean(), rho.max() / rho.mean(), "nan", "nan"])

    cwd = os.getcwd()
    os.chdir(out_dir)
    try:
        build_db.build(verbose=False)
    finally:
        os.chdir(cwd)
    print(f"wrote {out_dir}: sample_run ({n}^3), sample_stokes.fits, runs.json, results.db")
    print(f"next:  CATS_PROJECT_DIR={out_dir} python mcp_server_alexis.py")


if __name__ == "__main__":
    main()
