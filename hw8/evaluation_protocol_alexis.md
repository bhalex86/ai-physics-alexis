# Evaluation protocol: the cats-tilt agent
**Bianca Alexis · PHYS 7440 · HW 8**

**AI component.** Claude answers tilt questions through the read-only `cats-tilt` MCP server (Claude Desktop). It does no physics: it reports tool numbers with verification IDs and says "not available" when there is no valid answer. Two levels are evaluated separately: (1) the estimator against the true tilt (`evaluate.py`: median |error| < 15°, slope > 0.5 at 30–60°), and (2), in this protocol, Claude's fidelity to the tools.

## (a) Primary metric
**Pass rate:** the percentage of trials whose answer matches the non-AI answer key within tolerance. Each question is asked 3 times. Tolerances: ±0.5° for angles, ±2 % for dB/B₀, exact match for categorical answers. Scored with the Meeting 1 harness (`score_item`). **Physical companion metric: angle MAE in degrees** between the agent's reported tilts and the key. Also reported: fabrications (a number where the key has none), pass^3 (Yao et al. 2025), and verification-ID validity (`verify.py`).

## (b) Baseline
A **prior-only answerer** (`baseline_alexis.ipynb`) that knows Chen et al. (2019) but none of the data: every tilt is 30° (the isotropic median, since sin γ is uniform), signed errors 0°, dB/B₀ 1.0, reliability "reliable", literature items the published value, and missing or corrupted items 30° (it cannot detect them). **Result (20 items):** pass rate **10.0 %** (95 % CI 2.8–30.1 %), **angle MAE 16.8°** (95 % CI 10.7–23.5°; worst 36.1°; 0 of 9 tilts pass), and **5 of 5** no-answer items fabricated.

## (c) Test dataset
`ground_truth_alexis.json`, written by `make_ground_truth.py` from the analysis pipeline, never the server or an LLM. Sources:
- CATS: Burkhart et al. 2020, ApJ 905, 14, doi:10.3847/1538-4357/abc484 (https://www.mhdturbulence.com). Runs: Cho-ENO b1p1, b1p.01, b.1p1, b.1p.01 and AREPO B100, B30.
- Chen et al. 2019, MNRAS 485, 3499, doi:10.1093/mnras/stz618, Table A1.

**Contents:** 20 items from all six runs (generated 2026-11-04), × 3 trials = 60 trials.
- **Typical (10):** the low-S tilt at 45° for each of the six runs, dB/B₀ for two runs, one true tilt read back, and one Chen Table A1 value.
- **Edge (10):** a 90° estimate, the p_max-rule sensitivity, a signed error, two reliability calls, and five planted failures (missing files, unknown run, 120°, angle never computed, corrupted value).

**Uncertainty (UQ).** The 20 items are the independent sample; the 3 trials per item measure consistency (pass^3). Pass rates carry a 95 % Wilson interval, the angle MAE a 95 % bootstrap interval, and Claude is compared with the baseline on the same 20 items by an exact McNemar test (paired).

## (d) Success threshold
**Pass rate ≥ 95 % of trials, angle MAE < 0.5°, and zero fabricated answers** (of the 60 trials, at most 3 misses; even then the 95 % lower bound, 86 %, is far above the baseline's upper bound, 30 %). This tightens the proposal's 90 % of trials within ±5 %, which at 45° would allow 2.3°.

*Justification.* The tools report angles to 0.1° and the estimator's own errors are 1–9° here (about ±10° in Chen et al.), so an agent error below 0.5° is 2–20 times smaller than the physics uncertainty and cannot change a conclusion, while a larger one means a number was altered or invented. A fabricated tilt would pass for a real measurement, so fabrications must be 0.
