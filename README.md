# Testing a dust-polarization estimator of magnetic-field tilt in public MHD simulations

**PHYS 7440 project, Bianca Alexis.** Chen et al. (2019, MNRAS 485, 3499) proposed estimating how far a cloud's magnetic field is tilted out of the plane of the sky from how weakly its dust emission is polarized. This project tests that method on public MHD simulations (CATS: Cho-ENO and AREPO runs), plus an Athena++ run of my own, where the true tilt is known. A read-only MCP server, **cats-tilt**, does the physics, so that Claude can answer questions about it without inventing numbers. Every answer carries a verification ID that can be checked independently.

Branch: `week10observability`. The proposal (15 Oct 2026) is the reference for what "success" means.

---

## Key result

**The estimator works only at moderate field dispersion.** Pre-registered analysis: seven viewing angles, one snapshot per run, p_max = map maximum, graded over true tilts of 30–60°.

| Run | δB/B₀ | Median error (low-S) | Slope | Grade |
|---|---|---|---|---|
| CATS b1p1, b1p.01 | 0.51–0.58 | −1.9° | 0.75–0.81 | **PASS** |
| CATS b.1p1, b.1p.01 (turbulent) | 4.4–5.8 | +6 to +9° | ≈ 0.04 | **FAIL**: the estimate stays near 52° whatever the true tilt |
| Athena++, my own run (very ordered) | 0.21 | −19.0° | 0.62 | **FAIL**: biased low |

- **Turbulent fields** fail because depolarization along the line of sight, not the tilt, sets the polarization fraction. They would pass the pre-registered median-error test alone; the slope test (added in Week 7) catches them.
- **Very ordered fields** fail in the other direction. No sightline lies near the plane of the sky, so p_max underestimates p₀ and every tilt comes out too low. With the true p₀ the same Athena++ maps give 43° at a true 45°. Chen et al. note this limit in their Appendix A, but find their own least-perturbed model unaffected; these runs are not.

The full table, with 95% intervals, is in [`results/tilt_recovery_grades_alexis.md`](results/tilt_recovery_grades_alexis.md); the analysis is in [`PHYSICS_REVIEW_alexis.md`](PHYSICS_REVIEW_alexis.md).

> **Pending rebuild.** HW10 changed the method (cubic sampling of B, a KDE on every pixel, 5° tilts, three snapshots per run). The CATS numbers above still come from the earlier method; `make rebuild` recomputes everything where the data are. On the Athena++ run, the only one rebuilt so far, the estimates moved by at most 0.1°.

## Reproduce it

```bash
git clone https://github.com/bhalex86/ai-physics-alexis.git && cd ai-physics-alexis
git checkout week10observability
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make result                 # about 5 s, no download: figure, tables and the run's JSON log in results/
```

| Command | What it shows |
|---|---|
| `make check-reproducible` | two runs are identical, and match the committed files (tables byte for byte, figure pixel for pixel) |
| `make test` | 110 offline tests: the physics checked against Chen's equations, the pipeline, the server, the logging |
| `make trace` | the call tree of the run that made the committed result, read from its log |
| `make review-checks` | recomputes every number quoted in the review documents |
| `make rebuild` | recomputes everything from the CATS and AREPO data (needs the data; about 2 GB of downloads) |

## Connect the MCP server and the data to Claude Desktop

Claude reaches the data only through the cats-tilt server, which runs on your computer and reads the simulation cubes there; nothing is uploaded. You tell Claude Desktop two things: **how to start the server** (the Python in this repository's `.venv` plus `mcp_server_alexis.py`), and **which project folder** holds the data (`CATS_PROJECT_DIR`).

**1. Install** (once, in the repository folder): follow the first four lines of [Reproduce it](#reproduce-it), then check with `make test`.

**2. Choose the data.**
- *Sample data, no download:* `python make_sample_data.py` makes `sample_project/`, with a small synthetic cube called `sample_run`.
- *The real simulations:* use a project folder holding `runs.json` (which lists each run's four FITS cubes: density, Bx, By, Bz), the run folders with the cubes (e.g. `b1p1/dens_t900.fits.gz`; download them from CATS as in [`docs/mcp_server.md`](docs/mcp_server.md) section 9), and `results.db` (`python build_db.py --results stageAB_results.csv`). `runs.json` gives paths relative to the project folder.

**3. Get the three full paths** (run in the repository folder; for real data, use your project folder in the last line):
```bash
echo "$PWD/.venv/bin/python"
echo "$PWD/mcp_server_alexis.py"
echo "$PWD/sample_project"
```

**4. Edit Claude Desktop's config file.** On macOS: `open -e "$HOME/Library/Application Support/Claude/claude_desktop_config.json"`. On Windows: `%APPDATA%\Claude\claude_desktop_config.json`. If the file doesn't exist yet: Claude Desktop → Settings → Developer → Edit Config. Add the `cats-tilt` entry inside `"mcpServers"`, using your three paths:
```json
{
  "mcpServers": {
    "cats-tilt": {
      "command": "/Users/you/ai-physics-alexis/.venv/bin/python",
      "args": ["/Users/you/ai-physics-alexis/mcp_server_alexis.py"],
      "env": {"CATS_PROJECT_DIR": "/Users/you/ai-physics-alexis/sample_project"}
    }
  }
}
```
The file must stay **one** JSON object. If it already has other servers or a `"preferences"` section, add `"cats-tilt"` next to the other servers, with a comma between them; don't paste a second `{ … }` block. If your cubes are outside the project folder, add `"CATS_ALLOWED_DIRS": "/path/to/folder"` to `"env"`: the server reads `.fits` files only inside these folders. Check the file before restarting:
```bash
python3 -m json.tool "$HOME/Library/Application Support/Claude/claude_desktop_config.json" > /dev/null && echo "JSON OK"
```

**5. Restart Claude Desktop completely** (Cmd+Q, then reopen it; closing the window isn't enough). In a new chat, the tools menu should list **cats-tilt** with 13 tools.

**6. Check the connection.** Ask: *"List the runs available in cats-tilt, and whether their cube files are present."* Every run should show `cube_files_present: true`. With the project's `runs.json`, the planted missing run `b1p.32_t900` correctly shows `false`. Then ask for a computation *from the cubes*:

> *Using cats-tilt, compute the tilt of sample_run at 45° with make_map and then estimate_tilt (pmax_percentile 100). Do not use stored results. Give the estimate, the true tilt and the verification ID.*

**7. Verify what Claude reported** (in the repository folder, with the venv active):
```bash
export CATS_PROJECT_DIR="/same/folder/as/in/the/config"
python verify.py --list                                  # every tool call, in order
python verify.py <V-id> --expect tilt_deg_low_S=<value>  # VALID and MATCH = Claude quoted the tool faithfully
```

**If cats-tilt doesn't appear:** read the server's log, `tail -30 "$HOME/Library/Logs/Claude/mcp-server-cats-tilt.log"`.
- `ModuleNotFoundError`: `command` is not the `.venv` Python.
- `No such file`: one of the paths is wrong.
- A JSON error: a missing comma or brace in the config file.
- `FILE_NOT_FOUND` in a reply: `CATS_PROJECT_DIR` points at the wrong folder, or `runs.json` names files that aren't there.

**Next:** [`docs/claude_desktop.md`](docs/claude_desktop.md) has the test prompts for the real cubes (b1p.01, AREPO B30, b.1p.01), including a blind test of whether Claude recognizes turbulence-dominated runs and its control, plus how to score the answers.

## How results are checked

- **Every tool call is logged** as one JSON line: timestamp, tool, sanitized inputs, output summary, time in ms, success or failure (`obs_logging_alexis.py`). Read any log with `python trace_alexis.py <log>`.
- **Seeds and randomness:** every entry point seeds 42, and LLM calls use temperature 0. [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md) lists every source of randomness.
- **Claude's numbers are verified, not trusted.** Each tool result is signed with a key Claude never sees (`verify.py`). An answer counts only if its number appears in a signed tool call it cites.
- **Bugs:** [`BUG_LOG.md`](BUG_LOG.md) lists seven found and fixed, each with a regression test.

## For reviewers

| Question | Where | Command |
|---|---|---|
| 1. Can I reproduce the main result from the README alone? | [Reproduce it](#reproduce-it); Claude Desktop: [Connect](#connect-the-mcp-server-and-the-data-to-claude-desktop) | `make result`, then `make check-reproducible` |
| 2. Are seeds and API keys handled correctly? | [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md). Keys live in `.env` and `~/.cats_mcp_key`: git-ignored, never logged, never in the history | `make test` |
| 3. Is every quantitative claim traceable to an output? | Result: `results/`, from the hash-checked cache. Review numbers: `results/review_checks_alexis.txt`. Claude's answers: verification IDs | `make review-checks`, `python data_pipeline_alexis.py --verify` |
| 4. Is AI tool use documented? | [`AI_USE.md`](AI_USE.md) | none |

## What is in the repository

| | |
|---|---|
| **Physics** | `firstmap_b.py` (Stokes maps, Chen eqs. 1–10), `rotate_maps.py` (tilted views), `evaluate.py` (grading), `measure_turbulence.py`, `monte_carlo.py` (reproduces Chen's Table 1) |
| **Pipeline** | `data_pipeline_alexis.py` (CATS and Athena++ → hash-checked cache) and `rebuild_alexis.py` (recompute everything) |
| **MCP server** | `mcp_server_alexis.py` (13 tools) and `verify.py` (checks verification IDs) |
| **Evaluation of Claude** | `hw8/`: answer key built without any AI, harness, scoring, protocol |
| **Results** | `results/`, `cache/`, `stageAB_results.csv`, `provenance_alexis.json` |
| **Reference docs** | [`docs/mcp_server.md`](docs/mcp_server.md) (every tool, error code and option), [`docs/pipeline.md`](docs/pipeline.md) (sources, schema, failure handling, **changes since the proposal**), [`docs/claude_desktop.md`](docs/claude_desktop.md) |

**Not in the repository:** the simulation data (gigabytes; download from CATS, see [`docs/mcp_server.md`](docs/mcp_server.md) section 9), and private files: the signing key, API keys, the audit log, and `planted_failures.json`, the answer key's secret part.

## Data and citations

- **CATS** (Burkhart et al. 2020, ApJ 905, 14).
- **Cho-ENO runs** (Cho & Lazarian 2003, MNRAS 345, 325; Burkhart et al. 2009, ApJ 693, 250).
- **AREPO runs** (Mocz et al. 2017, ApJ 838, 40).
- **Athena++** (Stone et al. 2020, ApJS 249, 4), at commit `1395e2f`.
- **Method:** Chen et al. 2019, MNRAS 485, 3499.

## Known limits

- **Maps:** synthetic maps assume optically thin emission and uniform grain alignment. The density-dependent alignment rerun in the proposal is not done yet.
- **Regime boundaries:** δB/B₀ between 0.21 and 0.51, and between 0.64 and 4.4, is untested, so the regime boundaries are approximate.
- **Observed maps:** for Stokes maps the true tilt and δB/B₀ are unknown, and the "too ordered" regime cannot be flagged.
- **The HW8 answer key is public.** Score Claude Desktop with web search off and only cats-tilt connected (see `hw8/README.md`).
