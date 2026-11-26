# Annotated bibliography

**Bianca Alexis · PHYS 7440 · HW 8**

Citations are in APA style.

---

## 1. Hu, Lazarian, Wu & Fu (2024): CNN inclination angles

Hu, Y., Lazarian, A., Wu, Y., & Fu, C. (2024). Probing three-dimensional magnetic fields: II – An interpretable convolutional neural network. *Monthly Notices of the Royal Astronomical Society, 527*(4), 11240–11255. arXiv:2310.12555

**(a) AI method.** A convolutional neural network (mean-squared-error loss) that maps 22×22-cell subfields of thin ¹³CO and C¹⁸O velocity-channel maps to the plane-of-sky field angle, the inclination γ, and the Alfvén Mach number M_A.

**(b) Evaluation.** Median absolute deviation from the simulation truth on held-out ZEUS-MP runs (M_A ≈ 0.61 and 1.09) at inclinations of 30°, 60° and 90°: about 3° in γ for sub-Alfvénic runs and 9–14° for super-Alfvénic runs. A Planck comparison toward L1478 gives an alignment measure of 0.94.

**(c) Adequacy.** Reporting errors by regime is a strength: it exposes the super-Alfvénic degradation. The omission is the test set: two runs from the training code at three inclinations, so generalization to independent simulations is untested, and no analytic γ estimator (such as Chen's) is compared on the same maps.

**(d) What I will adapt.** I will report both the agent's pass rate and the estimator's median |error| split by regime (dB/B₀ < 1 versus ≥ 1), as a two-row table in my results.

---

## 2. Yao, Shinn, Razavi & Narasimhan (2025): τ-bench

Yao, S., Shinn, N., Razavi, P., & Narasimhan, K. (2025). τ-bench: A benchmark for tool-agent-user interaction in real-world domains. In *International Conference on Learning Representations (ICLR 2025)*. arXiv:2406.12045

**(a) AI method.** Function-calling LLM agents (including gpt-4o) using domain API tools under written policies, with a second LLM playing the user.

**(b) Evaluation.** A task succeeds only if the final database state equals the annotated goal state *and* the reply contains the required values. There are 115 retail and 50 airline tasks, each run at least 3 times. Reliability is measured with pass^k = E[C(c,k)/C(n,k)]. For gpt-4o, pass^1 is 61.2 % (retail) and 35.2 % (airline), and pass^8 in retail is below 25 %.

**(c) Adequacy.** The state-based check is objective (no LLM judge), and repeated trials expose inconsistency. But a passing trial may still break policy (the authors call the reward "necessary but not sufficient"), and simulated-user errors add noise. Retail and airline tasks also say little about scientific tool use.

**(d) What I will adapt.** Next to the pass rate, I will report pass^3 (the same formula with n = 3): the fraction of my questions Claude answers correctly in all three repeats.

---

## 3. Wang et al. (2025): MCP-Bench

Wang, Z., Chang, Q., Patel, H., Biju, S., Wu, C.-E., Liu, Q., Ding, A., Rezazadeh, A., Shah, A., Bao, Y., & Siow, E. (2025). MCP-Bench: Benchmarking tool-using LLM agents with complex real-world tasks via MCP servers. *NeurIPS 2025 Workshop on Scaling Environments for Agents*. arXiv:2508.20453

**(a) AI method.** Twenty LLMs act as agents on 28 live MCP servers (250 tools) across 104 single- and multi-server tasks.

**(b) Evaluation.** Rule-based checks on execution traces measure tool-name validity, schema compliance (gpt-5: 99.3 %) and execution success. An LLM judge (o4-mini) scores task completion, grounding, tool use and planning on a 1–10 rubric, averaged over five shuffled runs. Overall scores range from 0.749 (gpt-5) to 0.428 (llama-3.1-8b).

**(c) Adequacy.** Separating mechanical tool errors from reasoning errors is a strength, though a successful call says nothing about whether its number is physically right. Also, most outcomes have no ground-truth value, so the judge cannot catch a plausible wrong number; its coefficient of variation is still 15.1 %, and the judge model also generated the tasks.

**(d) What I will adapt.** From `audit_log.jsonl` I will report the fraction of Claude's tool calls that were schema-valid (no INVALID_INPUT error) and that succeeded. I will use no LLM judge: every score will come from my answer key.
