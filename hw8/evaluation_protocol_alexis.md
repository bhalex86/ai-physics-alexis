# Evaluation protocol: the cats-tilt agent
**Bianca Alexis · PHYS 7440 · HW 8**

**AI component.** Claude answers tilt questions through the read-only `cats-tilt` MCP server (Claude Desktop or `eval_harness.py --mode llm`). It does no physics: it reports tool numbers with verification IDs, flags unreliable regimes, and says "not available" when there is no valid answer. The physics tools are graded separately (median |error| < 15°, slope > 0.5 at 30–60°).

## (a) Primary metric
**Pass rate:** the percentage of trials whose answer matches the non-AI answer key within tolerance. Each question is asked 3 times (3 trials). Tolerances: ±0.5° for angles, ±2 % for dB/B₀, and an exact match for "reliable", "unreliable", "not available" and "invalid". Scored with the Meeting 1 harness (`score_item`, my `within_tolerance` rule). **Physical companion metric: angle MAE in degrees** between the agent's reported tilts and the key, since a wrong angle is what would corrupt a physics conclusion. Also reported: fabrications (a number where the key has none), pass^3 (Yao et al. 2025), and verification-ID validity (`verify.py`).

## (b) Baseline
A **prior-only answerer** (`baseline_alexis.ipynb`) that knows Chen et al. (2019) but none of the data: every tilt is 30° (the isotropic median, since sin γ is uniform), signed errors 0°, dB/B₀ 1.0, reliability "reliable", literature items the published value, and missing or corrupted items 30° (it cannot detect them). **Result (20 items, 2026-11-04):** a pass rate of **10.0 % of trials** (2 of 20 items: the literature value and one "reliable" call). Its **angle MAE is 16.8°** (n = 10; worst 36.1°), with 0 of 9 tilts passed, and it **fabricated an answer on 5 of 5** no-answer items. In Week 9, Claude without tools is a second baseline.

## (c) Test dataset
`ground_truth_alexis.json` (harness format), written by `make_ground_truth.py` from the analysis pipeline directly, never the server or an LLM. Sources:
- CATS: Burkhart et al. 2020, ApJ 905, 14, doi:10.3847/1538-4357/abc484 (https://www.mhdturbulence.com). Runs: Cho-ENO b1p1, b1p.01, b.1p1, b.1p.01 (density and field cubes) and AREPO B100, B30. Maps follow the assumptions of King et al. 2018 (MNRAS 474, 5122).
- Chen et al. 2019, MNRAS 485, 3499, doi:10.1093/mnras/stz618, Table A1.

**Contents:** 20 items from all six runs (generated 2026-11-04), × 3 trials = 60 trials. (M_A items are added when velocity cubes are present; not in this build.)
- **Typical (10):** the low-S tilt at 45° for each of the six runs, dB/B₀ for two runs, one true tilt read back, and one Chen Table A1 value.
- **Edge (10):** a 90° estimate far from the truth, the p_max-rule sensitivity, a signed error, two reliability calls, and five planted failures (missing files, unknown run, 120° angle, angle never computed, corrupted stored value).

## (d) Success threshold
**Pass rate ≥ 95 % of trials, angle MAE < 0.5°, and zero fabricated answers** (of the 60 trials, at most 3 misses).

*Justification.* The tools report angles to 0.1°, and the estimator's smallest physical systematic is 1–3° (the p_max rule), so an error above 0.5° is an altered or invented number, not rounding. Below 0.5°, the agent's error is 2–20 times smaller than the estimator's own errors (1–9° here, about ±10° in Chen et al.), so it cannot change a physical conclusion. A fabricated tilt would be reported as a measurement, so fabrications must be 0. **Procedure:** `eval_harness.py --mode tool` must match the key 100 %; then 3 Claude trials, IDs checked with `verify.py`.
