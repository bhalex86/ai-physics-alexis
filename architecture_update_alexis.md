# Architecture update: Week 7 map vs. the server as built

PHYS 7440 · B. Alexis · updated 2026-11-17

The Week 7 whiteboard map (*MCP Architecture Map: Dust-Polarization Inclination Agent*) was a design sketch. The server in
`mcp_server_alexis.py` changed after it. The PDF is kept as submitted, and this note lists every point where it no longer
matches the code. Where the two disagree, the code and `design_alexis.md` are correct.

## What changed

| Map (Week 7) | Server as built | Why it matters |
| --- | --- | --- |
| Two phases: physics runs offline, and "no tool runs physics". `make_map` is a lookup. | `make_map` and `estimate_tilt` build a map from the run's cubes on demand, with the same `rotate_maps.py` / `firstmap_b.py` code as the offline pipeline, and cache it for the session. `estimate_tilt_from_cubes` and `estimate_tilt_from_stokes` run the estimator on any FITS files in the allowed folders. `lookup_result` and `grade_run` read the stored results in `results.db`. | Claude still does no physics: it only calls tools. The tools are deterministic, so repeated questions get identical numbers. |
| Tools: `make_map`, `estimate_tilt`, `query_results`, optional `fetch_run`. | 13 tools: `cats_catalog`, `inspect_fits`, `estimate_tilt_from_cubes`, `estimate_tilt_from_stokes`, `get_call_log`, `read_source`, `list_runs`, `run_properties`, `load_cube`, `make_map`, `estimate_tilt`, `lookup_result`, `grade_run`. `query_results` became `lookup_result` (one run and angle) plus `grade_run` (aggregates over 30–60°). `fetch_run` was dropped. | Use the real tool names in the paper and in scored questions. |
| "Scored tools never touch the network; only optional `fetch_run` does." | Only `cats_catalog` uses the network (it lists the CATS archive; a timeout is retried once). Downloading data is done offline by `data_pipeline_alexis.py` (HW9), with tenacity retries. | Keep `cats_catalog` out of scored trials so that every repeat sees identical data. |
| Network errors `NETWORK_ERROR`, `FETCH_TIMEOUT`, `CHECKSUM_MISMATCH`. | `NOT_FOUND`, `TIMEOUT`, `NETWORK_ERROR`, `UPSTREAM_ERROR` (from `cats_catalog`). File integrity is checked by SHA-256 in the HW9 pipeline, not by the server. | |
| Invalid run: `isError: true`, code `INVALID_ARG`. | `UNKNOWN_RUN` for a run not in `runs.json`; `INVALID_INPUT` for an out-of-range argument (for example an angle outside 0–90°). The full list is `ERROR_CODES` in the server. | The demo in the map's Phase 3 returns `UNKNOWN_RUN`, not `INVALID_ARG`. |
| `make_map` returns `p_max`, `p0_true`, `p0_derived`, `sanity_ok`. | `make_map` returns `map_id`, `true_tilt_gamma_B`, `n_pixels`, `p_median`, `p_99.5th_percentile`, `p_max`. `p0_derived` comes from `estimate_tilt`. There is no `sanity_ok` flag: out-of-range values come back as a `warnings` list. | |
| Bad-number trap: "p_max above the ceiling 0.1579 → `sanity_ok = false`". | Since 2026-11-17, `range_warnings` flags `p_max > p0/(1 − p0/3) = 0.1579` (Chen et al. 2019, eq. 8, with the maps' p0 = 0.15) and `p0_derived > 0.15` as unphysical (test `test_pmax_above_eq8_ceiling_is_flagged`). Before that date only values outside 0–1 were flagged. The planted bad number in the test data is an impossible tilt (412°), flagged as outside 0–90°. | The trap the map describes now exists. |
| Missing run → `DATA_UNAVAILABLE`. | The server returns `UNKNOWN_RUN` (not registered), `FILE_NOT_FOUND` (registered, cubes absent) or `NO_DATA` (nothing stored). `DATA_UNAVAILABLE` is the status the HW9 pipeline stores for a run it refused. | The answer key should accept the server's codes. |
| `query_results` filters on the true tilt γ_B. | `lookup_result` takes a viewing angle. Each stored row reports `gamma_B_true`, and `grade_run` grades over true tilts of 30–60°. All project runs have their mean field along a box axis (unit vector (1, 0, 0) in every run), so the viewing angle and γ_B agree to better than 10⁻⁵ degrees. | The 30–60° window still refers to γ_B. |

## Unchanged

- Error has one meaning: `err = γ_obs − γ_B`, signed, against the true mean-field tilt (Chen et al. 2019, eqs. 14–15).
- Two different p0 values: the maps are built with p0 = 0.15, and the estimator derives p0 from p_max (eq. 9).
- Every reply carries a verification ID (`verify.py`).
- The planted failures and their answer key (`planted_failures.json`) stay private to the scorer.
