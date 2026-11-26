# How AI tools were used in this project

PHYS 7440, B. Alexis. The project uses AI in two different roles, which are kept apart:

1. **AI as the subject of the study.** Claude Sonnet 5.5 answers questions through the read-only MCP server, and the project measures whether it reports the tools' numbers faithfully (`hw8/evaluation_protocol_alexis.md`). Claude does no physics in that role: every number comes from a tool call with a signed verification ID.
2. **AI as a coding and writing assistant.** Claude (Anthropic) helped write code and documentation. This file documents that role.

Lines marked **(author to confirm)** describe earlier weeks from the README and the commit history; the author should check and complete them.

## How AI-written work was checked

- **Tests:** 115 offline tests (`make test`), including physics invariants that hold regardless of the code (`test_physics_alexis.py`: Chen eqs. 7–10, the eq. 8 ceiling, geometry invariance) and a regression test for every bug in `BUG_LOG.md`, each shown to fail before its fix.
- **Against the paper:** every implemented equation was compared with Chen et al. (2019), eqs. 1–10 and Table A1, and the Monte Carlo test reproduces their Table 1 (`monte_carlo.py`).
- **Reproduction:** `make check-reproducible`, and a fresh clone in a new virtual environment reproduces the committed result (tables byte for byte, figure pixel for pixel).
- **Traceability:** every number in the review documents is either read from the committed cache or recomputed by `review_checks_alexis.py` (output committed in `results/`).
- **Things the AI could not check:** the CATS archive was not reachable from its session, so the HW10 method has not yet been run on the CATS data (`make rebuild`), and the LLM harness was tested with a fake client only (no API key in that session).

## Rules followed

- No API key or signing key was ever given to an AI tool. `.env`, `~/.cats_mcp_key`, `planted_failures.json` and `audit_log.jsonl` are git-ignored, and a scan of the full git history found none of them and no key strings.
- During the Claude Desktop scoring runs, Claude gets only the `cats-tilt` MCP server: no web search, no filesystem connector, memory off (see `hw8/README.md`), because the HW8 answer key is public on GitHub.
