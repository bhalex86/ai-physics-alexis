# Physics and code review (HW10)

PHYS 7440 project, B. Alexis. Review of 26 Nov 2026, covering every module that computes physics: `firstmap_b.py`, `rotate_maps.py`, `evaluate.py`, `measure_turbulence.py`, `monte_carlo.py`, `sensitivity.py`, `prepare_arepo.py`, `check_arepo.py`, `check_p0_athena_alexis.py`, `sweep_turbulence_alexis.py`, `data_pipeline_alexis.py` (normalisation, Athena++ reader, Mach numbers), the physics parts of `mcp_server_alexis.py`, `build_db.py`, and `hw8/make_ground_truth.py`.

Every number in this review that is not read from the committed cache is recomputed by `review_checks_alexis.py` (`make review-checks`), with its output committed as `results/review_checks_alexis.txt`; section letters are cited next to each number.

Method: every equation was checked against Chen et al. (2019, MNRAS 485, 3499; arXiv:1810.10020). Every module's self-test was run. Each suspected problem was then measured on the committed Athena++ snapshot or on controlled synthetic cubes before anything was changed. The new tests in `test_physics_alexis.py` (30) and `test_mcp_server_alexis.py` (1) encode the results.

**Summary.** The implementation of Chen's estimator is correct. The main error is in the **conclusions drawn from it**: the project and the MCP server said the method works whenever δB/B₀ < 1, but the project's own data show it also fails when the field is *too* ordered (finding 1). Three further errors were fixed (findings 2–4). Six systematics are measured and documented but deliberately not changed, because changing them would alter the pre-registered method and the HW8 answer key (findings 5–10).

---

## Verified correct (no change)

| What | How it was checked |
|---|---|
| Stokes q, u (Chen eq. 1), p (eq. 2), N₂ = ∫n(cos²γ − 2/3) (eq. 3) | line by line against the paper; `stokes_maps` and `rotated_maps` use the same sign and component convention as eq. 1 |
| Uniform field, eq. 7; p_max = p₀/(1 − p₀/3), eq. 8; p₀ = 3p_max/(3 + p_max), eq. 9; inversion for cos²γ_obs, eq. 10 | paper; `test_inversion_round_trip`, `test_uniform_tilted_field_matches_eq7` (exact to 10⁻¹⁰ at 0–90°) |
| Dispersion S over the 8 nearest neighbours (eqs. 5–6) | paper; same arctan2 form |
| Low-S cut S < median S, γ_obs as the Gaussian-KDE peak | paper (Sec. 4.2 uses the median) |
| Chen Table A1 errors quoted by the server (e.g. L1: +19.5, +3.5, −3.6, −17.1°) and the abstract's "within 10°–25°" | paper, all 12 table values |
| Monte Carlo reproduction of Chen Table 1 | `monte_carlo.py --N 20000`: largest difference 0.005, which is sampling noise |
| Tilted-view geometry: right-handed frame, true tilt γ_B, exact grid sampling at 0° and 90° | `rotate_maps --selftest`; new `test_estimate_does_not_depend_on_which_axis_holds_the_field` (relabelling the box so the field lies along y gives identical maps) |
| Line-of-sight averaging only depolarizes: p ≤ eq. 8 ceiling, so derived p₀ ≤ true p₀ | new `test_no_map_exceeds_the_eq8_ceiling` (δB/B₀ = 0.1, 0.5, 3; 0°, 45°, 90°) |
| AREPO conversion: mass-conserving block average, div B axis matching, full-resolution slab sums | `prepare_arepo --selftest`, `check_arepo --selftest`; axis bookkeeping matches `firstmap_b.sky_components` |
| Athena++ reader (mesh-block assembly) and v_A = B/√ρ in code units (μ = 1) | code review; the pipeline's M_A for the Athena++ run is 0.41, as recorded |
| Cho-ENO sound speed c_s = √p from the run name | `measure_turbulence --selftest` |
| Your Athena++ diagnosis in `check_p0_athena_alexis.py` (p_max comes from the flattest sightline, not from a 0° one) | re-derived below; it is correct and generalises to finding 1 |

---

## Errors found and fixed

### 1. The method also fails when the field is too ordered, and the server said the opposite

**Severity: high (scientific conclusion; the server told the LLM a wrong reliability).**

`mcp_server_alexis.regime()` labelled every δB/B₀ < 1 as "the regime where the estimator worked (δB/B₀ = 0.1–0.64)". The README said the same. But the project's own six runs show a failure on the low-dispersion side as well (low-S estimates, p_max = map maximum; CATS and Athena++ rows from `results/review_checks_alexis.txt` section F, AREPO rows from `hw8/physics_check_alexis.csv`):

| Run | δB/B₀ | Derived p₀ (true 0.15) at 30/45/60° | Error at 30/45/60° | Grade |
|---|---|---|---|---|
| AREPO B100 | 0.12 | n/a | −5.7 / −9.2 / −14.2° | pass, only just; bias grows with tilt |
| Athena++ (own run) | 0.21 | 0.127 / 0.095 / 0.054 | −11.6 / −19.0 / −23.1° | **FAIL** |
| b1p.01 | 0.51 | 0.135 / 0.127 / 0.120 | +1.0 / −1.9 / −6.6° | PASS |
| b1p1 | 0.58 | 0.114 / 0.114 / 0.087 | +0.8 / −1.9 / −4.8° | PASS |
| AREPO B30 | 0.64 | n/a | +6.8 / +2.2 / −3.6° | PASS |
| b.1p.01, b.1p1 | 4.4, 5.8 | ≈ 0.07–0.11 | estimates stay near 51–54° | FAIL |

**This limit is stated by Chen et al. themselves** (2019, Appendix A). Measuring p_max "assumes that one of the observed sightlines has γ ≈ 0". For a well-ordered field closer to the line of sight, "the observed pmax may be significantly below the intrinsic maximum polarization fraction". The project's contribution is not the mechanism but its **size and consequence**. Chen et al. go on to say that in their least-perturbed model (L1) the method "works nicely … even when the magnetic field is almost aligned with the observer's line of sight", because γ is "better reflected by the difference between pmax and p". This project's Athena++ run (δB/B₀ = 0.21) contradicts that: it is off by −19° at 45° and fails the grade, and AREPO B100 (0.12) is off by −14° at 60°. Possible reasons, which a further run could separate: different field statistics (driven, sub-Alfvénic boxes rather than colliding-flow clouds), resolution (64³ for Athena++), and p₀ (0.15 here, 0.1 in Chen). This contrast is worth a paragraph in the paper.

**Mechanism.** Eq. 9 assumes that some sightline in the map has its field in the plane of the sky, so that p_max = p₀/(1 − p₀/3). In a very ordered field viewed at tilt γ, the flattest sightline is still tilted by about γ minus the field's angular spread. p_max is then eq. 7 evaluated at that angle, and the derived p₀ is too small. Eq. 10 then turns every p into too small a tilt, and the error grows with γ. Your `check_p0_athena_alexis.py` shows exactly this for the Athena++ box. With the true p₀, the same pipeline gives 43.2° at 45° (`gamma_obs_truep0_lowS`). The new test `test_very_ordered_field_is_biased_low_with_derived_p0` reproduces it on a controlled divergence-free cube: at δB/B₀ = 0.1 and 60°, the estimate is more than 10° low, and the known p₀ recovers it to within 3°.

**Two opposite biases.** With the **true** p₀, Chen et al. note that γ_obs is "the maximum possible inclination angle", because line-of-sight depolarization only lowers p (their Sec. 4.2 and Summary (i)). With the **derived** p₀, a second bias acts in the opposite direction. The derived p₀ can never exceed the true p₀ (line-of-sight averaging only depolarizes; a new test checks this), and in a very ordered field it is well below it, which pushes every tilt down. Where that second bias dominates, as in the Athena++ and AREPO B100 runs, the estimate behaves as a **lower bound**. The server's `biased_low` note says so in those terms.

**Fix.** `regime()` now has three classes, through a new `reliability` field. `ordered_field_dominates` keeps its old meaning for existing clients.
- `unreliable`: δB/B₀ ≥ 1.
- `biased_low`: δB/B₀ < `LOW_DISPERSION_LIMIT` = 0.35, with a warning that the estimate is a lower bound.
- `reliable`: in between.

The limit lies in the untested gap 0.21–0.51; a run in that range would pin it down. `hw8/make_ground_truth.py` uses the same rule. Its two reliability items (b1p1 at 0.58, b.1p.01 at 4.4) keep their answers, so the HW8 answer key is unchanged. The README's failure-mode section and the server's caveats are corrected.

**Limit.** S does not reveal this regime: median S is 0.6–1.3° for the Athena++ run and 0.5–1.6° for the passing b1p1. So for Stokes maps (observations) it cannot be flagged, and the caveat says so.

### 2. `measure_turbulence.py` computed a non-standard Alfvén Mach number

**Severity: medium (a wrong physical quantity, served by `run_properties`).**

M_A was the cell average of |v|·√ρ/|B|, which uses the *local total* field. The CATS catalogue (Burkhart et al. 2009, 2020) and this project's own pipeline (`alfven_speed`) use M_A = v_rms/v_A0 with v_A0 = |⟨B⟩|/√⟨ρ⟩. M_s was likewise ⟨|v|⟩/c_s rather than v_rms/c_s. When δB/B₀ is large, local |B| ≫ B₀ and the cell average collapses:

| δB/B₀ (synthetic cube, v_rms fixed) | 0.35 | 1.73 | 7.62 |
|---|---|---|---|
| old cell-mean M_A | 1.16 | 0.77 | 0.20 |
| standard v_rms/v_A0 | 1.29 | 1.29 | 1.29 |

(`results/review_checks_alexis.txt`, section D.) So a super-Alfvénic run such as b.1p.01 (catalogue M_A = 2.0) would have been reported as sub-Alfvénic. On the Athena++ run, the two definitions differ by 8% (0.379 vs 0.414).

**Fix.** A new `mach_numbers()` returns the standard M_s and M_A. The old values are kept, renamed `M_s_cellmean` and `M_A_cellmean`. The self-test and `test_mach_numbers_are_standard_and_match_the_pipeline` check that both modules agree. `turbulence.csv` must be regenerated; it is not in the repository (finding 3).

### 3. The README listed result files that are not in the repository

**Severity: medium (reproducibility).**

`stageAB_results.csv`, `stageAB_grades.csv`, `turbulence.csv` and `runs.json` were listed as committed, and the README's own command `build_db.py --results stageAB_results.csv` depends on them. They are on no branch. **Fix:** the README now says so, and gives the working alternative (`build_db.py --results cache/results_rotated.csv`, five runs). **Action for you:** commit these four files from your computer, after regenerating `turbulence.csv` (finding 2).

### 4. A hidden module-level setting chose the p_max rule

**Severity: low (a latent trap, no wrong stored result).**

`firstmap_b.estimate_from_maps` read the global `PMAX_PERCENTILE`, which defaults to 99.5, while the pre-registered rule is 100. Any caller that forgot to set the global silently used the other rule. `check_arepo.py`'s resolution check did. The pipeline changed the global and then restored it, which is fragile and not thread-safe. **Fix:** `estimate_from_maps` takes `pmax_percentile=` explicitly; the pipeline and `check_arepo.py` pass 100. The default is unchanged, so `rotate_maps.py`'s file-naming convention still holds. **Check:** the Athena++ rebuild gives the same cache hash as before the change (`c2cc4e22…`), and `--verify` passes on the committed cache.

(Bugs 1 and 2 in `BUG_LOG.md`, the CATS source URL and the evaluation tool loop, were found earlier in HW10.)

---

## Systematics: measured, then changed (second pass, same day)

Findings 5, 6, 8, 9 and 10 below were first only documented, then implemented on request. The status of each is given at its end. **The committed cache, figure and answer key still hold the old-method CATS numbers**, because the CATS archive could not be reached from this workspace. `python rebuild_alexis.py` recomputes all of them, with one command, on a machine that has the data (see "Rebuild" at the end).

### 5. KDE subsampling noise is larger than the answer-key tolerance

`kde_peak` draws 20 000 of the pixels with seed 0. A 256² map has 65 536 pixels, so every CATS estimate depends on that seed. On a synthetic divergence-free 192³ cube, 20 seeds spread the all-pixel estimate by **0.9° at 30°, 0.7° at 45° and 0.7° at 60°** (`results/review_checks_alexis.txt`, section A). That is larger than the answer key's 0.5° tolerance. The answer key is self-consistent only because the server and `make_ground_truth.py` share seed 0. **Done:** `kde_peak` (and `sensitivity.kde_peak_reflected`) now use every pixel (`max_n=None` by default). This takes 0.4 s per KDE on 65 536 pixels and removes the seed dependence entirely. The 64² Athena++ maps were never subsampled, so their numbers do not change.

### 6. Interpolation smooths intermediate tilts but not 0° and 90°

At 0° and 90° the tilted sightlines sample grid cells exactly; at 15–75° they use trilinear interpolation, which smooths the field. Shifting the sky grid by half a cell isolates the effect:

| Cube | Change in median S | Change in estimate |
|---|---|---|
| Athena++ (smooth, δB/B₀ = 0.21) | −1.5 to −5 % | ≤ 1.4° |
| synthetic, rough (δB/B₀ ≈ 1) | −8.5 to −22 % | ≤ 2.0° |

All from `results/review_checks_alexis.txt`, section B (order 1).

So the angle dependence of the error contains a small resolution artefact. It matters most for rough, turbulent fields and for S.

**Done:** `rotate_maps.rotated_maps` now samples **B with a cubic B-spline**. The spline interpolates, so it is exact at grid points, and each field is prefiltered once per map. Density stays **trilinear**. With the same half-cell test, the change in S drops to at most 2% (Athena++) and 4–6% (rough cube), from up to 22% (`results/review_checks_alexis.txt`, section B, order 3). Cubic sampling of density was rejected after measurement: on a lognormal field with max/mean ≈ 1000, 11.5% of cubic samples were negative, and cubic interpolation of ln ρ lost 10% of the mass. Trilinear sampling is positive and conserves the mean to 1.5% (`results/review_checks_alexis.txt`, section C). Cost: about 4.5× slower per map, roughly 30 s per tilt at 256³. On the Athena++ run, the new sampling changes the estimates by at most 0.1° and S by at most 2% (`results/review_checks_alexis.txt`, section E).

### 7. The passing runs pass partly because two biases cancel

The derived p₀ is low even in the passing runs (b1p1: 0.114 at 30°, 24% below 0.15), which biases the tilt **low**. With the true p₀ the same b1p1 map gives 40.4° at a true 30°, about 10° **high** (section F of `results/review_checks_alexis.txt`). Depolarization along the line of sight lowers p and biases it **high**. In b1p1 and b1p.01 the two nearly cancel, giving errors under 2° at 30–45°. A PASS therefore does not show that each assumption of the method holds. The `gamma_obs_truep0*` columns separate the two effects and are worth showing in the write-up.

### 8. The grade rests on three tilts and one snapshot per run

`evaluate.grade` fitted a slope to the points at 30°, 45° and 60° only, and each run was a single snapshot (CATS has t_500 to t_900). The slope had no uncertainty attached.

**Pre-registration.** The proposal fixed seven viewing angles and one snapshot per run. So the original grade stays the headline ("Pre-registered analysis" in `results/tilt_recovery_grades_alexis.md`, computed from the 15° tilts of each run's primary snapshot), and everything below is reported as an extension next to it. The deviation is listed in the README's "Changes since the proposal" table. The headline table also shows the pre-registered criterion on its own: the two turbulent runs pass it (median error +6 to +9°) even though their estimates do not follow the truth (slope ≈ 0.04). That is the reason for the slope test added in Week 7.

**Done:**
- Tilts are now 0–90° in 5° steps (`ANGLES`), so the grade uses 7 tilts per snapshot.
- Each CATS run has three snapshots (`CATS_SNAPSHOTS`: t_900/850/800, and t_750/700/650 for b.1p.01). `--all` builds all 12; `--primary` builds the HW9 set.
- `evaluate.grade_with_ci` bootstraps the maps (2000 resamples, seed 0). It gives 95% intervals on the median |error| and the slope, P(pass) (the fraction of resamples that pass), and the spread of the median error between snapshots.
- `make_key_result_alexis.py` pools each run's snapshots, plots the mean with a band for the snapshot range, and reports all of these.

The snapshot list needs checking against the archive the first time it is fetched: a missing snapshot is recorded as `data_status = missing` and does not stop the build.

### 9. Different p₀ from Chen

Chen et al. used p₀ = 0.1 for their simulations; this project uses 0.15 (King et al. 2018). The inversion uses the derived p₀, so the effect is second order, but the server's comparison with Chen's Table A1 errors is not like for like. Say so where the comparison is quoted. **Done:** the server's Chen block now carries a `p0_note` that says this.

### 10. Smaller points

- `firstmap_b.fake_cube` was not divergence-free, so the sample project's div-B label check had no meaning. **Done:** the perturbation is now B = ∇×A, built spectrally with the Nyquist modes removed. |∇·B|/δB is about 3 × 10⁻¹⁵, δB/B₀ is unchanged (0.52), and the label check now separates the right axes by 3.5× (it was 1.1×).
- The Gaussian KDE on 0–90° is not reflected at the boundaries. That is Chen's choice, and `sensitivity.py` already compares it with a reflected KDE; keep that comparison in the write-up.
- S depends on pixel scale, so the S values of the 64³ Athena++ maps and the 256³ CATS maps are not directly comparable.

---

## Rebuild (applies all the changes above to the CATS and AREPO results)

The archive `users.flatironinstitute.org` was not reachable from this workspace, so the CATS numbers could not be recomputed here. On your computer, in the project folder (with `runs.json`, the CATS run folders and `arepo_*/grid256/`):

```bash
python rebuild_alexis.py --plant-failures   # about 2 GB of new snapshots; roughly 1-2 h
python data_pipeline_alexis.py --build --verify --all   # must now print REPRODUCED
```

It runs, in order:
1. the pipeline, with all 12 CATS snapshots and the Athena++ run;
2. the AREPO runs, at the same tilts and p_max rules;
3. `turbulence.csv`, with the standard Mach numbers (the velocity cubes of the primary snapshots are downloaded if needed);
4. `stageAB_results.csv`, `stageAB_grades.csv` and `results.db`;
5. the HW8 answer key, which is replaced only if no run was skipped;
6. the figure.

Every step is logged. Then commit the files listed at the top of `rebuild_alexis.py`. Expect the CATS answer-key values to move by up to about 2° (the KDE and interpolation changes). This is why the key and the server must be regenerated together.
