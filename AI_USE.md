# How AI tools were used in this project

PHYS 7440, B. Alexis. The project uses AI in two different roles, which are kept apart:

1. **AI as the subject of the study.** Claude Sonnet 5.5 answers questions through the read-only MCP server, and the project measures whether it reports the tools' numbers faithfully (`hw8/evaluation_protocol_alexis.md`). Claude does no physics in that role: every number comes from a tool call with a signed verification ID.
2. **AI as a coding and writing assistant.** Claude (Anthropic) helped write code and documentation. This file documents that role.

Lines marked **(author to confirm)** describe earlier weeks from the README and the commit history; the author should check and complete them.

## By week

| Week / branch | What the AI assistant did | What I did and checked |
|---|---|---|
| Week 2 (`main`) | Claude Haiku 4.5 inside the notebook answered textbook problems (the subject of that assignment) | compared its answers with textbook values (`week02_alexis.ipynb`) |
| Week 7 (`week7mcpserver`) | "most of the code was generated using Claude Opus 5.5" (README) **(author to confirm which files)** | design (`design_alexis.md`), choice of physics and tests, running the server in Claude Desktop **(author to confirm)** |
| Week 8 (`week8eval`) | **(author to confirm)** | the answer key comes from `make_ground_truth.py`, which never calls an LLM; the baseline notebook |
| Week 9 (`week9pipeline`) | **(author to confirm)** | the Athena++ run (built and run on my laptop); the pipeline run on the CATS data; `check_p0_athena_alexis.py` diagnosis |
| **Week 10 (`week10observability`)** | Claude Opus 5.5 in a Claude agent session (26 Nov 2026), working from my instructions on a copy of the repository. It wrote: `obs_logging_alexis.py`, `trace_alexis.py`, `make_key_result_alexis.py`, `rebuild_alexis.py`, `review_checks_alexis.py`, the `Makefile`, `REPRODUCIBILITY.md`, `BUG_LOG.md`, `PHYSICS_REVIEW_alexis.md`, this file's Week 10 row, the HW10 sections of `README.md`, `test_observability_alexis.py`, `test_physics_alexis.py`, and the changes in the files listed in `BUG_LOG.md`. It found bugs 1–7 and the physics findings. The commits it made carry `Co-Authored-By: Claude Opus 5.5` and a link to the session. | I set the scope (the HW10 assignment, my proposal and Chen et al. 2019 as references), chose which changes to make (the pre-registered analysis stays the headline; implement the measured systematics), and decided what to upload. **(author: add what you reviewed or reran yourself)** |

## How AI-written work was checked

- **Tests:** 115 offline tests (`make test`), including physics invariants that hold regardless of the code (`test_physics_alexis.py`: Chen eqs. 7–10, the eq. 8 ceiling, geometry invariance) and a regression test for every bug in `BUG_LOG.md`, each shown to fail before its fix.
- **Against the paper:** every implemented equation was compared with Chen et al. (2019), eqs. 1–10 and Table A1, and the Monte Carlo test reproduces their Table 1 (`monte_carlo.py`).
- **Reproduction:** `make check-reproducible`, and a fresh clone in a new virtual environment reproduces the committed result (tables byte for byte, figure pixel for pixel).
- **Traceability:** every number in the review documents is either read from the committed cache or recomputed by `review_checks_alexis.py` (output committed in `results/`).
- **Things the AI could not check:** the CATS archive was not reachable from its session, so the HW10 method has not yet been run on the CATS data (`make rebuild`), and the LLM harness was tested with a fake client only (no API key in that session).

## Rules followed

- No API key or signing key was ever given to an AI tool. `.env`, `~/.cats_mcp_key`, `planted_failures.json` and `audit_log.jsonl` are git-ignored, and a scan of the full git history found none of them and no key strings.
- During the Claude Desktop scoring runs, Claude gets only the `cats-tilt` MCP server: no web search, no filesystem connector, memory off (see `hw8/README.md`), because the HW8 answer key is public on GitHub.
