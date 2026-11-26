# Data ingestion pipeline (HW9): reference

Sources, the fresh-clone run, the Athena++ build, the cache schema, failure handling, reproducibility rules, changes since the proposal and known limits. Back to the [README](../README.md).

The pipeline is the offline stage that feeds the read-only MCP server: it gets the data, normalises it, runs the
Chen et al. (2019) estimator at seven viewing angles with the existing `firstmap_b.py` / `rotate_maps.py`, and stores the
results in a SQLite cache. The MCP server itself is unchanged.

## The two sources

| | Source 1: CATS Cho-ENO | Source 2: Athena++ (my own run) |
|---|---|---|
| What | `dens`, `magx`, `magy`, `magz` FITS cubes, run `b.1p.01`, snapshot 750 (`--all` adds `b1p1_t900`, `b1p.01_t900`, `b.1p1_t900`) | one `.athdf` snapshot (64³, isothermal MHD, uniform B₀ along x, M_A ≈ 0.4) |
| How | HTTPS download from `users.flatironinstitute.org/~bburkhart/data/CATS/MHD/256/<run>/t_<snap>/` | built at the pinned commit `1395e2f543f4368d448cb1a2cf92ba1f910e3416` and run serially with a fixed seed; the pipeline reads the `.athdf` and calls the GitHub API for the commit record |
| Version | no version number exists; the SHA-256 of each file is the version | commit SHA + SHA-256 of the `.athdf` |
| Citations | Burkhart et al. 2020; Cho & Lazarian 2003; Burkhart et al. 2009; Portillo et al. 2018; Bialy & Burkhart 2020 | Stone et al. 2020 |

(CATS also lists an "Athena++" entry; it is a set of driven mixing-layer simulations and is **not** used here.)

## Run it from a fresh clone

```bash
git clone https://github.com/bhalex86/ai-physics-alexis.git && cd ai-physics-alexis
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# optional: a GitHub token raises the API limit from 60 to 5000 requests/hour
echo "GITHUB_TOKEN=your_token_here" > .env

python data_pipeline_alexis.py --fetch --build --verify --all
```

`--all` matters: the committed `provenance_alexis.json` records the cache hash for all four CATS runs plus the Athena++ run,
and `--build` checks its new hash against that record (`reproduces_previous_build` in the provenance file, and a
`REPRODUCED` / `NOT REPRODUCED` line from `--verify`). With a different run set the two hashes are not comparable and the check
is skipped. The committed hash `8f9c8db5…` was reproduced exactly on a second machine (Linux, Python 3.10, NumPy 2.2) from
the same raw files.

* `--fetch` downloads the CATS files (about 240 MB per run) and the GitHub commit JSON. Files already on disk are reused.
  Put previously downloaded CATS files in `<project>/b.1p.01/` (the layout `rotate_maps.py` uses), or point `--project` at that folder.
  To record a genuine access time for them, run once with `--force` to download them again.
* `--build` normalises, estimates, writes `cache/pipeline_cache_alexis.db` and updates `provenance_alexis.json`.
* `--verify` recomputes the cache hash and compares it with the hash in `provenance_alexis.json` (and, right after a `--build`, reports whether the build reproduced the hash recorded before it), and re-hashes every raw file.
  It exits 0 if everything matches and 1 otherwise.

Choose the source with `--source cats` (download from the CATS site only), `--source athena` (my Athena++ run only) or `--source both` (default).
Other options: `--all` (all four CATS runs), `--runs b.1p.01_t750`, `--no-github` (offline), `--force` (download again), `--project DIR`,
`--athena-file PATH`, `--help`.
Always pass the same run set to `--build` and `--verify`: each build rewrites the cache from scratch for the runs you ask for.

## The Athena++ box (or skip it and use the committed output)

The committed files in `data/athena/` are enough to run the pipeline without building Athena++:
`Turb.out2.00050.athdf`, `athena_run_provenance.json` (configure line, input-file hash, patch hash, compiler, M_A statistics),
`turb_mhd.patch` and `athinput.turb_mhd_64`. To reproduce the run:

```bash
git clone https://github.com/PrincetonUniversity/athena.git && cd athena
git checkout 1395e2f543f4368d448cb1a2cf92ba1f910e3416
git apply ../data/athena/turb_mhd.patch          # adds a uniform field B0 (problem/b0) to pgen/turb.cpp
python3 configure.py --prob=turb -b --eos=isothermal --flux=hlld -fft -hdf5 \
   --include=<hdf5 include dir> --lib_path=<hdf5 + fftw lib dir>     # see paths below
make clean && make -j4
mkdir run_64 && ./bin/athena -i ../data/athena/athinput.turb_mhd_64 -d run_64
```
Paths: Ubuntu `--include=/usr/include/hdf5/serial --lib_path=/usr/lib/x86_64-linux-gnu/hdf5/serial`; macOS with Homebrew
`--cxx=clang++-apple --include=$(brew --prefix)/include --lib_path=$(brew --prefix)/lib`; macOS without admin rights, with FFTW and HDF5
from Miniforge (`conda install -c conda-forge fftw hdf5`), as used for the committed run:
`--cxx=clang++-apple --include=$HOME/miniforge3/include --lib_path=$HOME/miniforge3/lib --cflag="-Wl,-rpath,$HOME/miniforge3/lib"`.
The run is serial with `rseed = 1234`. The output is single-precision HDF5 and takes about 8 minutes on an Apple-silicon laptop.
Re-running the *simulation* is reproducible only to tolerance on a different compiler or CPU, so the `.athdf` is treated as an
upstream artifact identified by its SHA-256; re-running the *pipeline* on it is reproducible exactly.

## Where things land

| Path | Content | Committed? |
|---|---|---|
| `cache/pipeline_cache_alexis.db` | SQLite cache: `datasets` (one row per run), `estimates` (run × angle × p_max rule), `maps` | yes |
| `cache/results_rotated.csv` | the estimates in the format `build_db.py` reads, to feed the MCP `results.db` (`python build_db.py --results cache/results_rotated.csv`) | yes |
| `cache/maps/<sha256>.npy` | Q, U, p, cos²γ maps, named by a SHA-256 of their inputs | no (regenerated) |
| `cache/raw/github_commit_1395e2f.json` | the GitHub API response | yes |
| `provenance_alexis.json` | per file and dataset: source URL, access time, version/hash, response headers, transformations; also the retry log, the missing-field logs, failed runs with their reason, and the cache hash | yes |
| `b.1p.01/…`, `b1p1/…` | downloaded CATS cubes | no (see `.gitignore`) |

## Normalised schema (table `datasets`)

`run_id` (e.g. `cho:b.1p.01:t750`, `athenapp:1395e2f:Turb.out2.00050`), `legacy_name` (the name `results.db` uses), `source`, `code`,
`snapshot`, `source_url`, `data_status` (`ok` / `missing` / `unreachable` / `unusable`), `grid_n`, SHA-256 of each raw file,
`rho_mean`, `B0` (= |⟨B⟩| before normalisation), mean-field unit vector, `field_axis`, `dB_over_B0`, `M_s`, `M_A`, `gamma_B_0deg`,
`bunit`, `alfven_convention`, `missing_fields`. ρ is stored normalised to ⟨ρ⟩ = 1 and B to |⟨B⟩| inside the maps; the tilt does
not depend on units. **M_A convention** (one function, `alfven_speed`): Athena++ code units have μ = 1, so v_A = B/√ρ; CATS has no
velocity cubes and no stated field units, so its M_s and M_A are NaN (logged as missing, not guessed). Their nominal values are published in the CATS paper (Burkhart et al. 2020, Table 2; files in code units): b.1p.01 M_s 7.0, M_A 2.0; b.1p1 M_s 0.7, M_A 2.0; b1p.01 M_s 7.0, M_A 0.7; b1p1 M_s 0.7, M_A 0.7. They are quoted here as catalogue values and are deliberately not written into the measured columns.

## Failure handling

| Failure | What happens |
|---|---|
| Timeout, connection error, a transfer cut off part-way (the whole download is retried and a short file is never kept), HTTP 429, HTTP 5xx, GitHub 403 with `X-RateLimit-Remaining: 0` or `Retry-After` | `tenacity`: 4 attempts (3 retries), exponential backoff 1, 2, 4 s plus up to 1 s random jitter; `Retry-After` is honoured (capped at 120 s). If it still fails the run becomes `data_status = unreachable` and the pipeline carries on. |
| HTTP 404 | never retried; the run becomes `data_status = missing`. A `.fits.gz` 404 falls back to the uncompressed `.fits`. |
| Missing or null field (FITS header key, `M_s`/`M_A`, Athena++ file without `-b` data) | logged with field, file and source URL; sentinel substituted: NaN for floats (stored as SQL `NULL`), `"UNKNOWN"` for strings, `-1` for counts; names listed in `missing_fields`. |
| Required inputs are unusable: no density or no B, NaN/inf values, non-positive density, zero mean field, a grid that is not an n×n×n cube, or a file that cannot be read | the estimator **refuses**: the run gets `data_status = unusable`, its rows are stored with `status = DATA_UNAVAILABLE` and no numbers, the reason is written to `provenance_alexis.json`, and the rows are left out of `results_rotated.csv`. An estimate that comes out NaN is refused the same way. |
| GitHub answers 200 with something that is not JSON | recorded as `unparseable_response`; the pipeline carries on with sentinels. |

## Reproducibility rules

No timestamps are stored in the cache; access times appear only in `provenance_alexis.json`. Rows are sorted and floats rounded to
6 decimals. `--verify` hashes the cache *content* (schema text plus every row of `datasets` and `estimates`, as sorted JSON), not the
raw bytes of the `.db` file, because SQLite writes its own library version into the file header and that differs between machines.
Map files are checked too but only reported (float bits can differ between CPUs). The notebook shows the recomputed and recorded
hashes side by side.

## Tests and notebook

```bash
ATHDF=data/athena/Turb.out2.00050.athdf pytest test_data_pipeline_alexis.py -v   # 28 offline tests: a fake local server injects 503, 429, 404, a hang,
                                                                   # cut-off downloads and GitHub 403s; plus sentinels, refusals (NaN, bad
                                                                   # grid, corrupt files), rerun hash, tamper detection and build_db.py
jupyter notebook lab_notebook_alexis.ipynb                         # Restart & Run All after the pipeline has run
```
The notebook (it keeps running if one run is unavailable) shows the raw HTTP headers, FITS header and GitHub JSON, the normalised rows, the comparison plot with the Week 8
baseline (flat 30°, a 1:1 line and a ±15° band over 30–60°) and a table of median |error| and slope for each run, and checks the
pipeline's `b.1p.01_t750` numbers against `physics_check_alexis.csv` from Week 8.

## Changes since the proposal (15 Oct 2026)

The proposal PDF is kept as submitted; these are the deviations, with the date each was made.

| Proposal | What the project does now | Since |
|---|---|---|
| Claude must match the answer key on ≥ 95% of questions; elsewhere "90% of trials within ±5%", "approximately 50 trials" | One threshold: ≥ 95% of 60 trials (20 questions × 3 repeats), angle MAE < 0.5°, zero fabricated answers (`hw8/evaluation_protocol_alexis.md`) | Week 8 |
| Physics success: median error < 15° for true tilts of 30–60° | Kept, reported as `pass_preregistered`. A second test, slope of estimate vs. truth > 0.5, was added because a constant guess near 45° can pass the first; `PASS` needs both (`evaluate.py`) | Week 7 (29 Oct) |
| "The cut-offs and beam size are adjustable" | The S cut (S < median S) and the p_max rule (`max` / `p99.5`) are adjustable; there is no beam smoothing: maps are at grid resolution, as in Chen et al. (2019) | not done |
| Rerun with density-dependent grain alignment | Not done yet | not done |
| Stage B: AREPO runs with gravity off and on | Two AREPO runs were analysed in Week 8 (`hw8/physics_check_alexis.csv`) but are not in this pipeline yet | open |
| Data: CATS Cho-ENO, AREPO, optional TIGRESS | Added an Athena++ run of my own (64³, sub-Alfvénic, M_A ≈ 0.4) as a controlled ordered-field case; TIGRESS not used | HW9 |
| MCP server as drawn in the Week 7 architecture map | Changed while it was built; see `architecture_update_alexis.md` | Week 7–9 |
| "Rotating each cube to seven viewing angles"; one snapshot per run (Table 1: 4 runs × 7 angles = 28 maps) | **Kept as the headline result** ("Pre-registered analysis" in `results/tilt_recovery_grades_alexis.md`): 0–90° in 15° steps, primary snapshot. Added an **extension**, reported beside it and never in its place: 5° steps and three snapshots per CATS run, with bootstrap 95% intervals and P(pass) | HW10 (26 Nov) |
| Synthetic maps "following the dust-polarization equations used by Chen et al. (2019)"; "the most common pixel value is the estimate" | The equations are unchanged (checked against the paper, `PHYSICS_REVIEW_alexis.md`). Two implementation details changed in HW10: tilted views sample B with cubic splines instead of trilinear interpolation (less smoothing at intermediate tilts), and the KDE peak uses every pixel instead of a random 20 000-pixel subsample | HW10 (26 Nov) |

## Known limits

* Memory: a 256³ run needs about 2 GB; each angle takes roughly 15–20 s (the figure from [`mcp_server.md`](mcp_server.md); with the HW10 cubic sampling, about 30 s).
* The CATS URL layout follows section 9 of [`mcp_server.md`](mcp_server.md); if CATS reorganises its folders, `cats_urls()` is the one function to change.
* The Athena++ box is 64³ against 256³ for CATS, and the two differ in code and regime, so differences between them are not
  attributable to the estimator alone.
