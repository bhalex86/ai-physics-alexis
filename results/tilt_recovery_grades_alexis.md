## Pre-registered analysis (proposal, 15 Oct 2026)

Seven viewing angles (0-90 deg in 15 deg steps), the primary snapshot of each run, p_max = map maximum. Pre-registered success: median error < 15 deg over true tilts 30-60 deg. PASS (added in Week 7) also requires slope > 0.5. 95 % intervals and P(pass) from 2000 bootstrap resamples of the maps.

| run | snapshots | tilt step | dB/B0 | regime | estimator | median error (deg) | median abs error (deg) [95 %] | slope [95 %] | P(pass) | pre-registered test (median error < 15) | grade |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Athena++ (own run) | Turb.out2.00050 | 15 | 0.21 | very ordered (biased low) | low-S | -19.0 | 19.0 [11.6-23.1] | 0.62 [0.51-0.73] | 0.25 | fail | FAIL |
| Athena++ (own run) | Turb.out2.00050 | 15 | 0.21 | very ordered (biased low) | all pixels | -17.5 | 17.5 [4.9-22.4] | 0.42 [0.16-0.67] | 0.00 | fail | FAIL |
| CATS b.1p.01 | t750 | 15 | 4.44 | turbulent | low-S | +9.0 | 9.0 [7.2-21.9] | 0.03 [-0.08-0.14] | 0.00 | pass | FAIL |
| CATS b.1p.01 | t750 | 15 | 4.44 | turbulent | all pixels | +12.3 | 12.3 [0.3-27.1] | 0.09 [0.01-0.16] | 0.00 | pass | FAIL |
| CATS b.1p1 | t900 | 15 | 5.80 | turbulent | low-S | +6.1 | 6.9 [6.1-21.8] | 0.04 [-0.05-0.13] | 0.00 | pass | FAIL |
| CATS b.1p1 | t900 | 15 | 5.80 | turbulent | all pixels | +12.6 | 12.6 [3.5-25.1] | 0.05 [-0.07-0.17] | 0.00 | pass | FAIL |
| CATS b1p.01 | t900 | 15 | 0.51 | moderate | low-S | -1.9 | 1.9 [1.0-6.6] | 0.75 [0.69-0.81] | 1.00 | pass | PASS |
| CATS b1p.01 | t900 | 15 | 0.51 | moderate | all pixels | -1.0 | 1.0 [0.9-3.0] | 0.87 [0.87-0.87] | 1.00 | pass | PASS |
| CATS b1p1 | t900 | 15 | 0.58 | moderate | low-S | -1.9 | 1.9 [0.8-4.8] | 0.81 [0.81-0.82] | 1.00 | pass | PASS |
| CATS b1p1 | t900 | 15 | 0.58 | moderate | all pixels | +3.9 | 3.9 [3.3-6.8] | 0.76 [0.33-1.19] | 0.75 | pass | PASS |

## Extension (HW10 review, not pre-registered)

Every stored tilt (5 deg steps after the HW10 rebuild) and every snapshot of each run, pooled. Snapshot spread (CSV) = range of the per-snapshot median signed error. Identical to the table above until the cache is rebuilt with the HW10 method.

| run | snapshots | tilt step | dB/B0 | regime | estimator | median error (deg) | median abs error (deg) [95 %] | slope [95 %] | P(pass) | pre-registered test (median error < 15) | grade |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Athena++ (own run) | Turb.out2.00050 | 15 | 0.21 | very ordered (biased low) | low-S | -19.0 | 19.0 [11.6-23.1] | 0.62 [0.51-0.73] | 0.25 | fail | FAIL |
| Athena++ (own run) | Turb.out2.00050 | 15 | 0.21 | very ordered (biased low) | all pixels | -17.5 | 17.5 [4.9-22.4] | 0.42 [0.16-0.67] | 0.00 | fail | FAIL |
| CATS b.1p.01 | t750 | 15 | 4.44 | turbulent | low-S | +9.0 | 9.0 [7.2-21.9] | 0.03 [-0.08-0.14] | 0.00 | pass | FAIL |
| CATS b.1p.01 | t750 | 15 | 4.44 | turbulent | all pixels | +12.3 | 12.3 [0.3-27.1] | 0.09 [0.01-0.16] | 0.00 | pass | FAIL |
| CATS b.1p1 | t900 | 15 | 5.80 | turbulent | low-S | +6.1 | 6.9 [6.1-21.8] | 0.04 [-0.05-0.13] | 0.00 | pass | FAIL |
| CATS b.1p1 | t900 | 15 | 5.80 | turbulent | all pixels | +12.6 | 12.6 [3.5-25.1] | 0.05 [-0.07-0.17] | 0.00 | pass | FAIL |
| CATS b1p.01 | t900 | 15 | 0.51 | moderate | low-S | -1.9 | 1.9 [1.0-6.6] | 0.75 [0.69-0.81] | 1.00 | pass | PASS |
| CATS b1p.01 | t900 | 15 | 0.51 | moderate | all pixels | -1.0 | 1.0 [0.9-3.0] | 0.87 [0.87-0.87] | 1.00 | pass | PASS |
| CATS b1p1 | t900 | 15 | 0.58 | moderate | low-S | -1.9 | 1.9 [0.8-4.8] | 0.81 [0.81-0.82] | 1.00 | pass | PASS |
| CATS b1p1 | t900 | 15 | 0.58 | moderate | all pixels | +3.9 | 3.9 [3.3-6.8] | 0.76 [0.33-1.19] | 0.75 | pass | PASS |
