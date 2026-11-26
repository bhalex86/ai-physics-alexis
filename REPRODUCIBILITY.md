# Reproducibility

**In short:** running the pipeline twice gives identical results. Every random number in the project comes from a fixed seed. Everything that can still differ between runs (timestamps, IDs, the LLM's wording) is kept out of the results, or checked by a hash.

## Check it yourself

| Command | Passes if |
|---|---|
| `make check-reproducible` | the key result, made twice, is identical, and matches the committed files (tables byte for byte, figure pixel for pixel) |
| `python data_pipeline_alexis.py --verify` | the committed cache still has the hash recorded when it was built, and every raw data file still has its recorded SHA-256. It prints `VERIFY OK` |
| `make test` | 110 tests pass, including two that build the pipeline twice and compare the results |
| `make rebuild`, then `python data_pipeline_alexis.py --build --verify --all` | a full rebuild from the CATS data matches the previous build. It prints `REPRODUCED` (needs the data) |

Last checked 26 Nov 2026 (Linux, Python 3.13): both runs identical, and a fresh clone in a new virtual environment matched the committed result.

## Seeds

Every main script starts by calling `seed_everything(42)` (in `obs_logging_alexis.py`), which runs `random.seed(42)` and `numpy.random.seed(42)`. The scripts are `data_pipeline_alexis.py`, `mcp_server_alexis.py`, `make_key_result_alexis.py`, `rebuild_alexis.py`, `review_checks_alexis.py`, `hw8/eval_harness.py` and `hw8/make_ground_truth.py`. The seeds are also written into the first line of every JSON log. The Makefile also sets `PYTHONHASHSEED=0`.

Most of the project's random numbers come from their own generators with fixed seeds (table below), not from these global ones. The global seeds fix everything else, for example the random wait between download retries.

## Every source of randomness

### Random numbers used in the analysis: fixed seeds

| Where | What is random | Seed | Does it affect a result? |
|---|---|---|---|
| `evaluate.grade_with_ci` | bootstrap resampling for the 95% intervals and P(pass) | 0 | only the intervals and P(pass), not the grades |
| `monte_carlo.py` | random field directions, for the check against Chen et al. Table 1 | 0 | only that check |
| `firstmap_b.fake_cube` | synthetic test cube (sample project, tests) | 0 (tests use other fixed seeds) | only the sample project and tests |
| `hw8/baseline_alexis.ipynb` | isotropic-prior check; bootstrap interval of the baseline | 0 and 1 | the baseline's interval |
| `measure_turbulence.py`, `check_arepo.py`, `prepare_arepo.py` | synthetic cubes for their self-tests | 3, 2, 1 | no, self-tests only |
| Athena++ simulation | turbulence driving | `rseed = 1234` in `data/athena/athinput.turb_mhd_64` | the simulation (see "Varies between machines" below) |

**Removed rather than seeded:** the tilt estimate's KDE used to analyse a random sample of 20 000 pixels. Changing that seed moved estimates by 0.7–0.9°, more than the answer key's 0.5° tolerance (`results/review_checks_alexis.txt`, section A). It now uses every pixel, so there is nothing random left in the estimate.

### Download retries: timing only

Failed downloads are retried after a random wait (`tenacity`). The global seed fixes these waits, and they change only *when* a retry happens, never the data stored.

### The LLM: as controlled as the API allows

`hw8/eval_harness.py` calls the model with **`temperature=0`** (change it with `EVAL_TEMPERATURE`), a fixed model name (`EVAL_MODEL`), `max_tokens=1024`, and at most 8 rounds of tool calls.

Temperature 0 makes replies close to deterministic, but **not guaranteed identical**: the API does not promise bit-identical output. So:
- every question is asked **3 times**, and the scores are pass rate and pass^3, with a confidence interval (`hw8/evaluation_protocol_alexis.md`);
- every request and reply is logged as JSON (model, response ID, text, tool calls), so any run can be inspected later;
- **numbers never come from the LLM.** They come from the tools, signed with a verification ID, and are checked with `verify.py`. Different wording cannot change a scored number without failing that check.

Claude Desktop, used for the HW8 scoring, has no temperature setting. It is the one uncontrolled source, and it is handled the same way: 3 repeats, and every number checked against the signed log.

### Random on purpose, never part of a result

| What | Why |
|---|---|
| The server's session code and signing key (`~/.cats_mcp_key`) | verification IDs must be unpredictable, or they could be forged |
| The run ID in each JSON log | to tell runs apart in a shared log file |

So verification IDs and run IDs differ every time. That is expected.

## Varies between runs or machines, but kept out of the results

| What | How it is handled |
|---|---|
| Timestamps | stored only in logs and in `provenance_alexis.json`, never in the cache or the results |
| CATS data (no version numbers) | every file's SHA-256 is recorded; `--verify` reports any change |
| Rerunning the Athena++ simulation | exact only on the same compiler and CPU, so its output file is committed and identified by its SHA-256. Rerunning the *pipeline* on it is exact |
| Floating-point rounding between computers | results are rounded to 6 decimals before hashing |
| The SQLite file itself | it contains the SQLite version, so the hash covers the table contents, not the file |
| The figure | every plotting setting is fixed in the script, so local matplotlib settings cannot change it. The PNG *bytes* still depend on the Pillow version, so the figure is compared by its pixels |
| Order of dictionaries and sets | everything is sorted before it is written |

## Not covered

- **Exact package versions.** `requirements.txt` gives minimum versions. The versions actually used are recorded in every log and in `provenance_alexis.json`. For an exact environment, run `pip freeze > requirements-lock.txt`.
- **GPU or multithreaded nondeterminism.** None of the code uses either.
