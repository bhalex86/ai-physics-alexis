# Bug log

PHYS 7440 project, B. Alexis. Failure-mode categories are from the class taxonomy: *silent failure, hallucination, context overflow, loop, non-reproducibility*.

---

## Bug 1: the recorded source URL of a CATS density cube is a 404

| | |
|---|---|
| **Found** | 26 Nov 2026 (HW10), while adding the JSON log to `data_pipeline_alexis.py` |
| **Failure mode** | **Silent failure.** No error, no warning; the pipeline exits 0 and `--verify` passes, but a provenance field is wrong. |
| **File** | `data_pipeline_alexis.py` (branch `week9pipeline`, commit `6ca05b3`) |
| **Lines affected** | 502, 549, 833, 847 (before the fix) → 529, 577, 870, 884, plus the new helper `cats_url_of` at 489 (after) |
| **Status** | Fixed; regression test `test_source_url_is_the_url_of_the_file_actually_used` in `test_data_pipeline_alexis.py` |

### What went wrong

CATS stores most cubes gzipped (`magx_t750.fits.gz`), but the density of run `b.1p.01` snapshot 750 exists **only uncompressed**, as `dens_t750.fits`. The fetch step handles this: it tries `.fits.gz`, gets a 404, and falls back to `.fits`. But four other places wrote the URL of a file with the hard-coded expression `cats_urls(run_folder, snap, comp)[0][0]`, which is **always the first candidate, the `.fits.gz` URL**, whatever file was actually on disk:

| Line (before) | Function | What it recorded |
|---|---|---|
| 502 | `fetch_cats_run`, branch for a file already on disk | `files[...].url` in `provenance_alexis.json` |
| 549 | `load_cats_cubes` | `source_url` of the missing-`BUNIT` log entry |
| 833 | `build_cats_run` | `datasets.source_url` **in the SQLite cache**, and the `source_url` of every missing-field log entry for that run |
| 847 | `build_cats_run`, re-recording a local file | `files[...].url` in `provenance_alexis.json` |

### How it showed up

In the committed cache (`cache/pipeline_cache_alexis.db`), the dataset row for `cho:b.1p.01:t750` says:

```
source_url = https://users.flatironinstitute.org/~bburkhart/data/CATS/MHD/256/b.1p.01/t_750/dens_t750.fits.gz
```

That URL returns HTTP 404. The file that was actually read is `b.1p.01/dens_t750.fits`, from `.../dens_t750.fits`, which is the URL `provenance_alexis.json` correctly lists under `files["b.1p.01_t750:rho"]` (that record came from the download branch, which was right). So the cache and the provenance file disagreed, and the cache pointed to a file that does not exist. The `missing_field_log_build` entries for this run in `provenance_alexis.json` carry the same wrong URL.

It was worse for the setup the README recommends: copying previously downloaded cubes into `b.1p.01/` and running without `--force`. Then lines 502 and 847 also wrote the 404 URL into the `files` records, so *no* record pointed to the real file.

Nothing caught it, for three reasons. The URL is only metadata: the estimator never uses it, so every number is right. `--verify` checks that the cache matches *itself*, not that its URLs resolve. And the existing test (`test_missing_header_field_logged_with_sentinel`) only checked that the URL started with `http`.

### Fix

A new helper chooses the URL whose file name matches the file actually on disk:

```python
def cats_url_of(local, run_folder, snap, comp):
    cands = cats_urls(run_folder, snap, comp)      # [(.fits.gz url, name), (.fits url, name)]
    if local is not None:
        for url, fname in cands:
            if Path(local).name == fname:
                return url
    return cands[0][0]                             # file not on disk: the URL tried first
```

All four lines now call `cats_url_of(<the local file>, ...)` instead of `cats_urls(...)[0][0]`. Line 526 (the record of a *failed* download) still records the first URL tried, which is correct there. `PIPELINE_VERSION` goes from 1.0 to 1.1.

The regression test runs the fake CATS server, which serves the density uncompressed as the real archive does. It fetches the run, then fetches again so the files count as pre-existing, then builds. It checks that every recorded URL ends in the name of the file on disk. It also GETs `datasets.source_url` and checks that the bytes it returns have the SHA-256 stored in the cache. Before the fix the test failed at the first check (`.../dens_t750.fits.gz`); after the fix all 28 pipeline tests pass.

### Effect on committed outputs

No tilt, grade or figure changes: the URL is not an input to any calculation. The committed cache was **not** rebuilt in this commit: the rebuild needs the four CATS runs (about 1 GB), which I could not download from this environment. On the next `make pipeline`, the `source_url` of `cho:b.1p.01:t750` becomes `.../dens_t750.fits`. That changes one row, so the cache content hash changes once. The first `--build --verify` after the fix will therefore print `NOT REPRODUCED` against the old hash `8f9c8db5…`. This is expected. Running `make pipeline` a second time must then print `REPRODUCED`.

---

## Bug 2: an agent stuck calling tools was scored as "no answer"

| | |
|---|---|
| **Found** | 26 Nov 2026 (HW10), while adding the JSON log to `hw8/eval_harness.py` |
| **Failure mode** | **Loop**, reported as a different failure (so also a silent failure of the evaluation) |
| **File** | `hw8/eval_harness.py` (branch `week8eval`, commit `28b52cc`) |
| **Lines affected** | 77 and 94 (before) → `MAX_TOOL_ROUNDS` constant, the new `ToolLoopCapReached` exception (line 89), the `raise` at line 152 and its handler at line 314 (after) |
| **Status** | Fixed; test `test_eval_harness_reports_a_tool_loop_not_no_answer` in `test_observability_alexis.py` |

### What went wrong

`answer_with_llm` lets the model make tool calls for at most 8 rounds (`for _ in range(8)`, line 77). If the model is still asking for tools after round 8, the function fell through to `return "", n_calls` (line 94). The empty text then went to `parse_answer`, which found no JSON, and `score_item` recorded `failure = "no answer"`. So a model **looping** on tool calls (for example, calling `list_runs` over and over) was reported exactly like a model that **gave up**. Those two failures need different fixes: a loop calls for better tool descriptions or stopping rules, giving up calls for a better prompt. The run printed no warning either way.

### Fix

The loop's fall-through now raises `ToolLoopCapReached("still calling tools after 8 rounds (N tool calls)")`. `run()` catches it and records `failure = "tool loop: ..."`, so the per-item table and the JSON results say *loop*. The cap is the named constant `MAX_TOOL_ROUNDS`. A `tool_loop_cap_reached` event is written to the JSON log, next to the 8 logged `anthropic.messages.create` calls that led to it. The test uses a fake model that never stops asking for tools. It checks that the row's failure starts with `tool loop` and that the log holds exactly 8 LLM calls and the cap event. Before the fix, that row's failure was `no answer`.

### Effect on committed outputs

Pass rates and errors do not change: an item that hits the cap was a failure before and is a failure now. Only its label changes, from `no answer` to `tool loop`. If any earlier LLM-mode result listed `no answer` items, re-running them now shows which of those were loops.

---

## Bugs 3–6: found in the physics review

These four were found in the physics review of 26 Nov 2026. The full analysis, with measurements, is in `PHYSICS_REVIEW_alexis.md` (findings 1–4).

| # | Bug | Failure mode | Lines (before → after) | Fix | Test |
|---|---|---|---|---|---|
| 3 | The server labelled every δB/B₀ < 1 as the reliable regime. The project's own Athena++ run (δB/B₀ = 0.21) fails by −19° at 45°, and AREPO B100 (0.12) is −14° at 60°. Too ordered a field makes p_max underestimate p₀, so the tilt comes out low; Chen et al. (2019, Appendix A) state this limit, and the code ignored it. Claude was told an estimate was reliable when the project's data say it is biased. | **Silent failure** (a wrong statement served as fact; the LLM would repeat it) | `mcp_server_alexis.py` 98, 567–578, 738 → 98–105, 577–606, 767; `hw8/make_ground_truth.py` 170 → 173–174 | Three-way `regime()` with a `reliability` field (`reliable` / `biased_low` / `unreliable`) and a lower-bound warning below δB/B₀ = 0.35; the same rule in the answer key (its two items are unchanged) | `test_regime_rule_matches_the_six_tested_runs`, `test_very_ordered_field_is_flagged_biased_low`, `test_very_ordered_field_is_biased_low_with_derived_p0` |
| 4 | `measure_turbulence.py` computed M_A as the cell mean of \|v\|√ρ/\|B\| using the local total field, not v_rms/(\|⟨B⟩\|/√⟨ρ⟩). With δB/B₀ = 7.6 this gives 0.20 instead of 1.29 (`results/review_checks_alexis.txt`, section D), so a super-Alfvénic run reads as sub-Alfvénic. It disagreed with the pipeline's own M_A. | **Silent failure** | `measure_turbulence.py` 86–89 → 93, 97–115 | New `mach_numbers()` with standard M_s and M_A; the old values kept as `*_cellmean` | `--selftest`, `test_mach_numbers_are_standard_and_match_the_pipeline` |
| 5 | The README listed `stageAB_results.csv`, `stageAB_grades.csv`, `turbulence.csv` and `runs.json` as committed. They are on no branch, so `build_db.py --results stageAB_results.csv` fails from a clone. | **Non-reproducibility** | `README.md`, the file table in section 2 | README corrected, with a working alternative; the files must be committed from the author's computer | n/a (documentation) |
| 6 | `firstmap_b.estimate_from_maps` took the p_max rule from a module global that defaults to 99.5 (the pre-registered rule is 100). `check_arepo.py` silently used 99.5; the pipeline changed and restored the global. | **Non-reproducibility** (a hidden-state hazard; no stored number was wrong) | `firstmap_b.py` 32, 144 → 32–34, 137–150; `data_pipeline_alexis.py` (`estimate_run`); `check_arepo.py` 118, 120 | Explicit `pmax_percentile=` argument; the callers pass 100 | `test_explicit_pmax_rule_equals_module_setting`; the Athena++ cache hash is unchanged (`c2cc4e22…`) |

## Bug 7: regenerating the answer key could silently shrink it

| | |
|---|---|
| **Found** | 26 Nov 2026, while testing `rebuild_alexis.py` |
| **Failure mode** | **Silent failure** |
| **File / lines** | `hw8/make_ground_truth.py`, `main()`: runs without cubes are skipped (lines 102–107 of the HW8 version), and the output file is overwritten regardless |
| **What went wrong** | Run in a folder where some cubes were missing, it replaced the 20-item `ground_truth_alexis.json` with a 5-item one, printing only a "skipped" note. The HW8 evaluation would then have scored Claude on a quarter of the questions without noticing. |
| **Fix** | `rebuild_alexis.py` writes the new key to a temporary file and replaces the committed key only when `_skipped` is empty (`--allow-skipped` overrides). Tested on a project with no cubes: the committed key was left untouched. |
