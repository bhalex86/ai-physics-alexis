# cats-tilt: an MCP server for magnetic-field tilt from dust polarization
This along with most of the code was generated using Claude Opus 5.5 . Please note that the data files were used from a local folder
`mcp_server_alexis.py` lets an LLM client such as Claude Desktop estimate how far a cloud's magnetic field is tilted out of the plane of the sky. It uses the dust-polarization method of Chen et al. (2019, MNRAS 485, 3499) on public MHD simulations from the CATS archive (Burkhart et al. 2020, ApJ 905, 14).

The server does the physics; the LLM calls the tools, can read the code behind them, and reports what they return. Every reply carries a **verification ID** that you can check independently with `verify.py`.

Part of the PHYS 7440 project *Testing a dust-polarization estimator of magnetic-field inclination in public MHD simulations* (B. Alexis). The design is in `design_alexis.md`.

---

## 1. Quick start from scratch

No data download needed. About two minutes:

```bash
git clone <this repository's URL> && cd <repository folder>
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python make_sample_data.py                     # writes sample_project/ (4 MB, ~5 s)
pytest test_mcp_server_alexis.py -v            # 28 passed, 1 skipped
CATS_PROJECT_DIR=sample_project python mcp_server_alexis.py   # starts; waits for a client on stdio
```

`make_sample_data.py` builds a small **synthetic** 64³ cube (fixed random seed, mean field along x), a Stokes-map file, a `runs.json` registering the cube as `sample_run`, and a `results.db` of stored results. That is enough to exercise every tool. Its numbers test the software, not the physics: the sample run's grade comes out FAIL, which is expected for this toy cube.

The skipped test calls the real CATS archive over the internet; enable it with `CATS_LIVE=1`.

This sequence was checked in a fresh virtual environment containing only the server files listed in section 2.

## 2. Requirements and files

**Python** 3.10 or newer (tested with 3.13). Dependencies are in `requirements.txt`:

- `mcp` 2.x: the server uses the 2.x `MCPServer` API, which differs from mcp 1.x
- `numpy`, `scipy`, `astropy`, `pandas`
- `certifi`: needed on macOS with python.org Python to verify HTTPS certificates
- `pytest`, `jsonschema`: for the tests

**All of these files must be in one folder:**

| File | Role |
|---|---|
| `mcp_server_alexis.py` | the MCP server |
| `firstmap_b.py` | Stokes maps and Chen's estimator (eqs. 1–3, 9, 10) |
| `rotate_maps.py` | tilted viewing angles |
| `evaluate.py` | grading of stored results |
| `prepare_arepo.py` | field-component check (∇·B = 0) |
| `build_db.py` | builds `results.db` from result CSVs |
| `make_sample_data.py` | builds the from-scratch sample project |
| `verify.py` | checks verification IDs |
| `test_mcp_server_alexis.py` | tests |
| `requirements.txt` | dependencies |
| `design_alexis.md` | design document |

> **The server is not a single self-contained file.** `mcp_server_alexis.py` imports `firstmap_b.py`, `rotate_maps.py`, `evaluate.py` and `prepare_arepo.py`. Without them in the same folder it stops at startup with `ModuleNotFoundError`.

### What is in this repository, and what is not

Besides the server files above, the repository holds the rest of the analysis pipeline and its results, so the project's numbers can be reproduced as well as the server:

| File | Role |
|---|---|
| `measure_turbulence.py` | measures δB/B₀, Mach numbers and density statistics of each run |
| `check_arepo.py` | checks AREPO field labels and the 512³ → 256³ averaging |
| `sensitivity.py` | how much the p_max rule and peak method change the estimates |
| `monte_carlo.py` | reproduces the Monte Carlo test of Chen et al. (2019), Table 1 |
| `test_evaluate.py` | tests of the grading criteria |
| `make_comparison_pdf.py` | builds the one-page comparison with Chen et al. |
| `stageAB_results.csv`, `stageAB_grades.csv` | the project's results: 6 runs × 7 tilts × 2 p_max rules, and their grades |
| `turbulence.csv` | measured turbulence properties of each run |
| `runs.json` | the runs and snapshots used |
| `.gitignore` | keeps the files below out of the repository |

Some files are deliberately **not** committed. Each is either downloadable, generated, or private:

| Not in the repository | Why | How to get it |
|---|---|---|
| FITS and HDF5 simulation data (`b1p1/`, `arepo_B30/`, …) | gigabytes | download from the CATS archive (section 9) |
| `results.db` | generated | `python build_db.py --results stageAB_results.csv`, or `python make_sample_data.py` for the sample |
| `sample_project/` | generated | `python make_sample_data.py` |
| `audit_log.jsonl` | local: contains paths from the author's computer | created on each machine by the server |
| `planted_failures.json` | the answer key for scoring the LLM | kept private by the author |
| `.env`, `~/.cats_mcp_key` | secrets (an API key, the signing key) | each user's own; the key is created on first run |

**Checking reproducibility:** clone the repository into an empty folder and follow section 1. If the tests pass there, the code runs without anything from the author's computer.

**Peer review** (Weeks 10 and 12) happens as pull requests on this repository.

## 3. Running the server

**On its own:** `python mcp_server_alexis.py` starts it on stdio. It prints nothing and waits for an MCP client.

**In Claude Desktop:** add this to `~/Library/Application Support/Claude/claude_desktop_config.json`, then quit and reopen Claude Desktop:

```json
{
  "mcpServers": {
    "cats-tilt": {
      "command": "/full/path/to/python3",
      "args": ["/full/path/to/mcp_server_alexis.py"],
      "env": {"CATS_PROJECT_DIR": "/full/path/to/sample_project"}
    }
  }
}
```

- **`command`:** the Python the requirements are installed in (`which python3` inside the virtual environment).
- **`env`:** drop this line to use your own data instead of the sample; the server then uses the folder it sits in.

## 4. Environment variables

All are optional.

| Variable | Default | Purpose |
|---|---|---|
| `CATS_PROJECT_DIR` | folder of `mcp_server_alexis.py` | where `runs.json`, `results.db` and relative FITS paths are looked up |
| `CATS_ALLOWED_DIRS` | (none) | extra folders FITS files may be read from, separated by `:` |
| `CATS_KEY_FILE` | `~/.cats_mcp_key` | secret signing key; created on first run with permissions 600 |
| `CATS_AUDIT_LOG` | `<project folder>/audit_log.jsonl` | signed log of every tool call |
| `CATS_BASE_URL` | `https://users.flatironinstitute.org/~bburkhart/data/CATS/` | CATS archive root |
| `CATS_HTTP_TIMEOUT` | `20` | seconds per CATS request |
| `CATS_HTTP_RETRY_DELAY` | `1` | seconds before the one retry |
| `CATS_LIVE` | (unset) | tests only: `1` runs the live CATS test |

No API keys or credentials are needed: the CATS archive is public.

## 5. Tools

The two core endpoints are `cats_catalog` (external API) and `estimate_tilt_from_cubes` (dataset).

| Tool | Inputs | What it returns | Source |
|---|---|---|---|
| **`cats_catalog`** | `folder` | sub-folders and files in one CATS folder | CATS archive over HTTPS |
| **`estimate_tilt_from_cubes`** | `density`, `bx`, `by`, `bz`, `line_of_sight`, `angle`, `pmax_percentile` | tilt estimate, true tilt, field regime, uncertainty, comparison with Chen | FITS cubes (+ `results.db`) |
| `estimate_tilt_from_stokes` | `stokes_i`, `stokes_q`, `stokes_u`, `pmax_percentile` | tilt estimate and uncertainty from 2-D maps | FITS maps (+ `results.db`) |
| `inspect_fits` | `path` | HDUs, shapes, header keywords, SHA-256 | FITS file |
| `read_source` | `name` | the code and docstring of a physics function, and the Chen equation it implements | the project's modules |
| `list_runs` | none | approved runs, whether their files exist, what is stored | `runs.json`, `results.db` |
| `run_properties` | `run_id` | stored turbulence measurements (δB/B₀, M_s, M_A) | `results.db` |
| `load_cube` | `run_id` | summary of a run's cubes | FITS cubes |
| `make_map` | `run_id`, `angle` | summary of one synthetic map, and its `map_id` | FITS cubes |
| `estimate_tilt` | `map_id`, `pmax_percentile` | estimate for that map | FITS cubes |
| `lookup_result` | `run_id`, `angle`, `pmax_rule` | one stored result | `results.db` |
| `grade_run` | `run_id`, `pmax_rule` | grade: median \|error\| and slope over 30–60° | `results.db` |
| `get_call_log` | `last_n` | this session's calls, in order, with verification IDs | audit log |

Every input is described in the tool's published JSON Schema, with its range or allowed values: for example `angle` is 0–90, `line_of_sight` one of `z`, `y`, `x`, `tilted`, and `pmax_percentile` 90–100. The full schemas of the two core tools are in `design_alexis.md`.

**Key conventions**
- **Tilt γ:** 0° means the field lies in the plane of the sky; 90° means it points along the line of sight.
- **`line_of_sight`:** `z`, `y` or `x` views the cube straight down that axis, as stored (the default is `z`). `tilted` builds a view with the mean field tilted by `angle`.
- **`pmax_percentile`:** 100 (the default) takes p_max as the map maximum, as in Chen et al. and this project's pre-registered rule. 99.5 uses the 99.5th percentile.
- **FITS paths:** absolute, or relative to the project folder. Only files inside the project folder (or `CATS_ALLOWED_DIRS`) can be read, and names must end in `.fits` or `.fits.gz`.

## 6. Example tool calls and expected outputs

Verification IDs and timestamps differ on every run; the other values are what you should see.

**`list_runs`** (sample project), with `{}`:
```json
{"verification_id": "V-c544-…", "call_number": 1,
 "runs": [{"run_id": "sample_run", "code": "synthetic", "gravity": false, "snapshot": "seed 0",
           "description": "synthetic 64^3 turbulent cube, mean field along x (not a CATS simulation)",
           "cube_files_present": true,
           "stored_results": {"max": [0.0, 15.0, 30.0, 45.0, 60.0, 75.0, 90.0],
                              "p99.5": [0.0, 15.0, 30.0, 45.0, 60.0, 75.0, 90.0]}}]}
```

**`lookup_result`** (sample project), with `{"run_id": "sample_run", "angle": 45}`:
```json
{"verification_id": "V-c544-…", "call_number": 2, "source": "db:results:4",
 "run": "sample_run", "rule": "max", "angle": 45, "gamma_B_true": 45.0,
 "gamma_obs": 40.2, "gamma_obs_lowS": 41.8, "err": -4.8, "err_lowS": -3.2, "...": "..."}
```

**`estimate_tilt_from_cubes`** on real CATS data: Cho-ENO run `b.1p.01`, snapshot `t_750` (download in section 9), with `{"density": "b.1p.01/dens_t750.fits", "bx": "b.1p.01/magx_t750.fits.gz", "by": "b.1p.01/magy_t750.fits.gz", "bz": "b.1p.01/magz_t750.fits.gz", "line_of_sight": "tilted", "angle": 45}`. Takes about 15 s. Shortened output:
```json
{"verification_id": "V-443a-001-9663808f53c1", "call_number": 1,
 "tilt_deg_low_S": 54.0, "tilt_deg_all_pixels": 57.3, "true_tilt_deg": 45.0,
 "uncertainty_deg_low_S": 6.1,
 "view": {"line_of_sight": "tilted", "angle_deg": 45.0, "tilted_toward_axis": "z"},
 "pmax_percentile": 100.0,
 "field_regime": {"dB_over_B0": 4.438, "ordered_field_dominates": false,
                  "note": "the turbulent field is 4.4 times the mean field: ... treat this estimate as unreliable"},
 "uncertainty": {"low_S": {"components_deg": {"pmax_rule_sensitivity": 5.1,
                                              "estimator_spread_all_vs_lowS": 3.3,
                                              "project_calibration_median_abs_err": null}, "...": "..."},
                 "comparison_with_chen_2019": {"citation": "Chen C.-Y. et al. 2019, MNRAS 485, 3499",
                     "nearest_tabulated_tilt_deg": 60.0,
                     "error_at_that_tilt_deg": {"L1 (least turbulent)": -3.6, "L10": -6.5,
                                                "L20 (most turbulent)": -9.5}, "...": "..."},
                 "caveats": ["the turbulent field is 4.4 times the mean field: ...", "..."]},
 "warnings": ["the turbulent field is 4.4 times the mean field: ... treat this estimate as unreliable"]}
```
This run is turbulence-dominated, the regime where the method fails, and the reply says so. `project_calibration_median_abs_err` is `null` here because no `results.db` was present.

The top-level `tilt_deg_*`, `true_tilt_deg` and `uncertainty_deg_low_S` fields duplicate the nested values for clients that read a single key. The course's `eval_harness.py --mode tool` was run against this server this way and scored it correctly.

**`cats_catalog`**, with `{"folder": "MHD/256/b1p1/"}`. This needs internet; the folders shown are what the real archive lists for this run:
```json
{"verification_id": "V-…", "call_number": 3,
 "url": "https://users.flatironinstitute.org/~bburkhart/data/CATS/MHD/256/b1p1/",
 "folders": ["t_500/", "t_550/", "t_600/", "t_650/", "t_700/", "t_750/", "t_800/", "t_850/", "t_900/"],
 "files": [], "n_entries": 9, "retrieved_at": "2026-10-29T13:20:04-0500"}
```

**`read_source`**, with `{"name": "firstmap_b.gamma_from_p"}`:
```json
{"verification_id": "V-…", "name": "firstmap_b.gamma_from_p", "signature": "gamma_from_p(p, p0)",
 "implements": "Chen eq. 10 (tilt gamma from p and p0)", "first_line": 108, "last_line": 112,
 "file": {"path": ".../firstmap_b.py", "size_bytes": 9903, "sha256": "…"},
 "source": "def gamma_from_p(p, p0):\n    \"\"\"Chen eq. 10, clipped to the physical range.\"\"\"\n    cos2 = p * (1 + 2 * p0 / 3) / (p0 * (1 + p))\n    ..."}
```
With `{}` it returns an index of every readable function and the Chen equation each implements. Only the five project modules can be read.

**`get_call_log`**, with `{}`:
```json
{"verification_id": "V-…", "session": "c544",
 "calls": [{"call_number": 1, "tool": "list_runs", "verification_id": "V-c544-001-…", "ok": true},
           {"call_number": 2, "tool": "lookup_result", "verification_id": "V-c544-002-…", "ok": true}]}
```

## 7. Errors

**Missing or wrongly typed arguments** are rejected by the MCP SDK against the tool's schema before the tool runs. The reply has `is_error: true` and names the field. For example, `lookup_result` with `{"run_id": "sample_run"}` gives "angle: Field required".

**Every other failure** has `is_error: true` and a JSON body:
```json
{"error": {"code": "NO_DATA",
           "message": "no stored result for run 'sample_run', angle 20, rule 'max'; stored angles: [0, 15, 30, 45, 60, 75, 90]",
           "tool": "lookup_result", "verification_id": "V-c544-003-a90983383806"}}
```

| Code | Meaning |
|---|---|
| `INVALID_INPUT` | right type, invalid value (e.g. `angle` 120, a 2-D file where a cube is needed) |
| `UNKNOWN_RUN` | `run_id` is not in `runs.json` |
| `PATH_NOT_ALLOWED` | a file is outside the allowed folders |
| `FILE_NOT_FOUND` | a named file, or a run's cube file, does not exist |
| `NO_DATA` | valid request, but nothing is stored for it |
| `NOT_FOUND` | the CATS archive has no such folder (HTTP 404) |
| `TIMEOUT` | the CATS archive did not answer in time, after one retry |
| `NETWORK_ERROR` | the CATS archive could not be reached (DNS, connection, certificates) |
| `UPSTREAM_ERROR` | the CATS archive returned another HTTP error |
| `SERVER_NOT_CONFIGURED` | `runs.json` or `results.db` is missing |
| `UNKNOWN_NAME` | `read_source` was asked for a module or function it does not expose |
| `INTERNAL_ERROR` | an unexpected exception, reported instead of crashing |

Failed calls are signed and logged like successful ones.

## 8. Verifying what the LLM reported

Every call is signed with HMAC-SHA256 using a secret key on your computer (`~/.cats_mcp_key`), which the LLM never sees, and appended to `audit_log.jsonl`. So the LLM cannot invent a valid ID, and an edited log entry fails the check.

```bash
python verify.py --list                                       # this session's calls, in order
python verify.py V-443a-001-9663808f53c1 --files --expect tilt_deg_low_S=54.0
```

| Result | Meaning |
|---|---|
| `VALID` | the call happened and its log entry is unaltered |
| `INVALID` | the entry was edited, or signed with a different key |
| `NOT FOUND` | no tool call produced this ID, e.g. the LLM made it up |
| `unchanged` / `CHANGED since` (with `--files`) | whether the input files are the ones used, by SHA-256 |
| `MATCH` / `MISMATCH` (with `--expect`) | whether a number the LLM quoted is what the tool returned; add `--tol 0.51` if it rounded to whole degrees |

## 9. Using the real CATS data

Download a run's four files into the project folder, for example Cho-ENO `b1p1` at snapshot `t_900`:

```bash
mkdir -p b1p1 && for f in dens magx magy magz; do
  curl -C - -o b1p1/${f}_t900.fits.gz \
    https://users.flatironinstitute.org/~bburkhart/data/CATS/MHD/256/b1p1/t_900/${f}_t900.fits.gz
done
```

- **Find what exists:** `cats_catalog` lists runs and snapshots. Some folders store density uncompressed (`dens_t750.fits`).
- **Estimate the tilt:** pass the four files to `estimate_tilt_from_cubes`. Each is about 60 MB; a call takes 15–20 s and about 1 GB of memory.
- **AREPO runs:** these come as 512³ HDF5 files. Convert them to 256³ FITS first with `prepare_arepo.py`.
- **Stored-result tools:** add the runs to `runs.json`, run `rotate_maps.py` on them, then `python build_db.py`.

**Data citations requested by CATS:**
- Cho & Lazarian 2003, MNRAS 345, 325
- Burkhart et al. 2009, ApJ 693, 250
- Burkhart et al. 2020, ApJ 905, 14
- Mocz et al. 2017, ApJ 838, 40 (AREPO runs)

## 10. Tests

```bash
pytest test_mcp_server_alexis.py -v                      # 28 passed, 1 skipped, ~2 s
CATS_LIVE=1 pytest test_mcp_server_alexis.py -k live     # also call the real CATS archive
```

The tests run on a small fake project in a temporary folder, with a temporary key and audit log, so your real files are never touched.

- **Valid inputs:** every tool, with each output checked against an expected JSON Schema.
- **Invalid inputs:** every tool, each required to return a structured error with the right code.
- **Network failures, simulated:** a timeout (retried once, then reported), HTTP 404, and no connection.
- **Planted failures:** a missing run, an impossible stored value (flagged, not hidden), and a question with no stored answer.
- **Physics checks:** the default and tilted views give the right true tilt, and a turbulence-dominated cube is flagged as unreliable.
- **Verification:** genuine IDs pass, forged IDs and edited log entries fail.

## 11. Live demo (Meeting 2)

1. Run `pytest test_mcp_server_alexis.py -q` and show 28 passed.
2. In Claude Desktop, show that the `cats-tilt` tools are listed.
3. A successful call: *"List the snapshots of CATS run b1p1, then estimate the tilt of the b1p1 t_900 cubes viewed at 45°, with its uncertainty compared to Chen. Show me the code that turns p into the tilt, and list the tools you used, in order."*
4. An error case: *"Look up the stored result for sample_run at 120 degrees."* It returns `INVALID_INPUT`.
5. In Terminal: `python verify.py --list`, then `python verify.py <id> --expect tilt_deg_low_S=<value>` on a number from Claude's answer.

## 12. Known failure modes

- **Turbulence-dominated fields.** When the turbulent field outweighs the mean field, the estimate stays near 45–58° whatever the true tilt. For cubes the server measures δB/B₀ and warns above 1. This project's runs passed at δB/B₀ = 0.1–0.64 and failed at 4.4–5.8; values in between are untested. For Stokes maps δB/B₀ is unknown, so only estimates in the failing range are flagged.
- **Very ordered fields.** p₀ is then set by the few most polarized pixels, so the result depends on `pmax_percentile`. The `pmax_rule_sensitivity` component reports how much.
- **Idealized maps.** They assume optically thin emission and uniform grain alignment, with no noise or telescope beam. `estimate_tilt_from_stokes` does not de-bias noisy observations.
- **Mislabelled field files** give a wrong tilt. The ∇·B check runs on every cube; the anisotropy check runs only when the ordered field dominates. A warning is added if either fails.
- **File names.** Names must end in `.fits` or `.fits.gz`. Some uploads rename `magx_t750.fits.gz` to `magx_t750_fits.gz`, which is rejected; rename it back.
- **Network.** The CATS archive may be slow or unreachable, giving `TIMEOUT` or `NETWORK_ERROR` after one retry. On macOS, missing certificates also show as `NETWORK_ERROR`; `pip install certifi` fixes it.
- **Speed and memory.** A 256³ cube takes 15–20 s and about 1 GB. Larger cubes should be block-averaged first.
- **Stored-result tools** need `runs.json` and `results.db`; without them they return `SERVER_NOT_CONFIGURED`. The cube and Stokes tools still work, without the calibration part of the uncertainty.
- **Verification is local.** It proves the server produced a value, not that the physics is right, and anyone who can read `~/.cats_mcp_key` can forge IDs. Don't give the LLM file access to your home folder.
