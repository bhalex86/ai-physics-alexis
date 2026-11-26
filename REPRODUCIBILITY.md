# Reproducibility: every source of randomness and how it is controlled

PHYS 7440 project, B. Alexis. HW10. This file lists everything in the project that could make two runs differ, and what fixes it. The rule throughout is: **a random choice gets a fixed, written-down seed; everything else that can vary (timestamps, IDs, upstream data, float bits) is kept out of the results, or pinned by a hash that `--verify` checks.**

## How to check it yourself

| Command | What it shows |
|---|---|
| `make check-reproducible` | runs the key-result script twice and compares the outputs (tables byte for byte, figure pixel by pixel), then compares them with the versions committed in git |
| `make test` | includes `test_pipeline_logs_every_tool_and_reruns_identically` (two pipeline builds of the Athena++ run give the same cache hash) and `test_end_to_end_byte_identical_and_verify` (fetch + build in two folders, same hash) |
| `python data_pipeline_alexis.py --verify` | recomputes the cache's content hash and compares it with `provenance_alexis.json`; re-hashes every raw file on disk |
| `make pipeline` (needs the CATS archive) | full rebuild; `--verify` then reports `REPRODUCED` if the new cache matches the previous one |

Results of these checks on 26 Nov 2026 (Linux, Python 3.13, NumPy 2.5, matplotlib 3.11): `make check-reproducible` gave 4 of 4 files identical run to run. A fresh clone with a new virtual environment reproduced the 3 committed tables byte for byte and the committed figure pixel for pixel. The two Athena++ builds gave the same cache hash. The full 5-run cache hash `8f9c8db5…` was reproduced exactly on a second machine in HW9.

## Seeds set at the top of every entry point

Each main script calls `obs_logging_alexis.seed_everything(42)` right after its imports. That runs `random.seed(42)` and `numpy.random.seed(42)`, and records them in the `run_start` line of the JSON log:

| Script | Where |
|---|---|
| `data_pipeline_alexis.py` | `SEEDS = obs.seed_everything(42)` after the imports |
| `mcp_server_alexis.py` | after `mcp = MCPServer(...)` |
| `hw8/eval_harness.py` | after the imports |
| `hw8/make_ground_truth.py` | after the imports |
| `make_key_result_alexis.py` | after the imports |

The Makefile also exports `PYTHONHASHSEED=0`. The code sorts every set and dict before writing it, so output does not depend on this, but it removes the question.

**What these two global seeds actually control.** The project's own random draws do *not* use the global generators. Each uses a local `numpy.random.default_rng(seed)` with a fixed seed (table below), which `numpy.random.seed(42)` does not touch. The global seeds fix two other things. One is tenacity's retry jitter, which uses Python's `random`. The other is any library code that draws from the global NumPy generator without its own seed; none is known.

## Every source of randomness

### 1. Random numbers in the analysis code (all fixed seeds)

| Where | What is random | Seed | Affects results? |
|---|---|---|---|
| `firstmap_b.kde_peak` | **nothing since HW10**: every pixel is used. Up to HW9 a random subsample of 20 000 pixels (`default_rng(0)`) was drawn for every 256² map; 20 seeds spread a synthetic estimate by 0.7–0.9° (`results/review_checks_alexis.txt`, section A), more than the answer key's 0.5° tolerance. The review removed this source of randomness rather than just fixing its seed. | (none; `max_n=` restores subsampling with `seed`) | no longer |
| `evaluate.grade_with_ci` | bootstrap resampling of the maps for the 95% intervals and P(pass) | `default_rng(0)`, 2000 resamples | the intervals and P(pass) in `results/`; not the grades themselves |
| `sensitivity.kde_peak_reflected` | none since HW10 (every value is used, as in `kde_peak`) | (none) | no longer |
| `firstmap_b.fake_cube(seed=0)` | synthetic turbulent cube (divergence-free since HW10) | 0 by default; `make_sample_data.py` uses 0; tests use 1–6 | sample project and tests only |
| `monte_carlo.simulate(seed=0)` | random field directions (von Mises) | `default_rng(0)` | the Monte Carlo reproduction of Chen et al. Table 1 |
| `measure_turbulence.py` self-test | synthetic cube | `default_rng(3)` | self-test only |
| `check_arepo.py` self-test | synthetic cube | `default_rng(2)` | self-test only |
| `prepare_arepo.py` self-test | synthetic cube | `default_rng(1)` | self-test only |
| `hw8/baseline_alexis.ipynb` | isotropic-prior check (10⁶ random vectors); bootstrap CI of the baseline MAE (10 000 resamples) | `default_rng(0)`; `default_rng(1)` | the baseline's confidence interval |
| `test_mcp_server_alexis.py` | synthetic cube | `default_rng(4)` | tests only |
| `test_data_pipeline_alexis.py` | `os.urandom(5000)` as the body of a deliberately cut-off download | not seeded | no: the test only checks that the short file is rejected, whatever its bytes |

### 2. Retries (timing only)

`data_pipeline_alexis.py` retries HTTP requests with exponential backoff plus up to 1 s of random jitter (`tenacity.wait_random`, which uses Python's `random`). With `random.seed(42)` the jitter sequence is fixed. It changes only *when* a request is retried, never what is stored, so outputs are unaffected either way. Retries are logged with their wait times (`retry_attempt`, `retry_wait_s` in the JSON log, and `retry_log` in the provenance).

### 3. The LLM

| Setting | Value | Where |
|---|---|---|
| temperature | **0** (lowest available), override with `EVAL_TEMPERATURE` | `hw8/eval_harness.py`, `TEMPERATURE`, passed to every `client.messages.create` |
| model | `EVAL_MODEL`, default `claude-sonnet-5-5` | same file; the exact model string the API returns is logged per response |
| max_tokens | 1024 | same |
| tool rounds per question | at most 8 (`MAX_TOOL_ROUNDS`); hitting the cap is reported as a loop (BUG_LOG.md, bug 2) | same |

**Temperature 0 is not a guarantee of identical output.** It makes the model pick its most likely token at each step, but the API does not promise bit-identical replies: batching and hardware can change near-ties, and a model alias can move to a new snapshot. The project controls this in three ways:
1. The evaluation protocol (`hw8/evaluation_protocol_alexis.md`) asks each question 3 times and reports pass rate and pass^3 with a confidence interval, not a single run.
2. Every LLM request and reply is logged: model, response ID, stop reason, the text and the tool calls it made. So any run can be inspected after the fact.
3. The *numbers* never come from the LLM. They come from the MCP tools, are signed with a verification ID, and are checked with `verify.py`. A different wording from the model cannot change a scored value without failing that check.

To pin the model, set `EVAL_MODEL` to a dated snapshot name rather than an alias. To replay a run exactly in tests, the offline tests use a fake client (`test_observability_alexis.py`). `vcr.py` / `pytest-recording` could record real API responses the same way; this is not set up, because it would commit API traffic to the repository.

**Claude Desktop** (`hw8/score_desktop.py`, the Week 9 scoring) has no temperature setting, so it is the one *uncontrolled* source of randomness in the project. It is handled the same way: 3 trials per question, and every number checked against the signed audit log.

### 4. Deliberately random, never part of a result

| What | Why it is random | Where it appears |
|---|---|---|
| MCP `SESSION` (`secrets.token_hex(2)`) and the signing key `~/.cats_mcp_key` (`secrets.token_hex(32)`) | security: verification IDs must be unforgeable, so they cannot be predictable | verification IDs, `audit_log.jsonl` |
| `RUN_ID` of each process (time + `uuid4`) | tells runs apart in a shared log file | JSON logs only |

So verification IDs and run IDs differ on every run. This is expected; no result depends on them.

### 5. Non-random things that still vary between runs

| What | How it is controlled |
|---|---|
| Timestamps (access times, log times) | Kept only in `provenance_alexis.json` and the logs, never in the cache or the result files. The cache hash covers content only. |
| Upstream CATS data (no version number) | SHA-256 of every file in `provenance_alexis.json`; `--verify` reports `MISMATCH (upstream data changed)`. |
| My Athena++ run | Serial run with `rseed = 1234` (`data/athena/athinput.turb_mhd_64`) at pinned commit `1395e2f`. Re-running the *simulation* on another compiler or CPU reproduces it only to tolerance, so the output `.athdf` is committed and identified by its SHA-256; re-running the *pipeline* on it is exact. |
| Floating-point bits across CPUs / NumPy builds | Cache floats rounded to 6 decimals; the hash is over the rounded rows. The `.npy` map files can differ in the last bits; they are checked but only reported. |
| SQLite file bytes | The `.db` header contains the SQLite library version, so `--verify` hashes the rows, not the file. |
| The figure PNG | No timestamp, `Software` metadata removed. **Local matplotlib settings** are overridden: the script calls `matplotlib.rcdefaults()`, then sets explicitly every setting that changes pixels (font DejaVu Sans, which ships with matplotlib, `text.hinting`, dpi, anti-aliasing). This was a real problem. The machine that first made the figure injected its own defaults (font Inter, `text.hinting = no_hinting`, `figure.dpi = 200`), and these survive `rcdefaults()`. So a fresh venv on the same computer, with the same matplotlib and FreeType versions, drew a different PNG. The fresh-clone check found this on 26 Nov 2026. After the fix, the fresh venv and the original machine draw **identical pixels**. The PNG *bytes* still differ, because Pillow 12.3 and 13.0 compress the same image differently. So `make check-reproducible` compares tables byte for byte and figures pixel by pixel. A different matplotlib or FreeType version could still move anti-aliased pixels, and the check would report it. The CSV and Markdown tables are the exact record. |
| Dict and set order | All outputs are sorted before writing. |

## Not covered

- GPU or multithreaded nondeterminism: none of the code uses either.
- Library upgrades: versions are recorded in every `run_start` log line and in `provenance_alexis.json`. `requirements.txt` sets minimum versions, not exact ones. For a bit-exact environment, run `pip freeze > requirements-lock.txt` on the machine that produced a result.
