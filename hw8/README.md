# HW8: evaluation protocol, architecture, bibliography and baseline

**Bianca Alexis · PHYS 7440**

This project tests the Chen et al. (2019) dust-polarization estimator of magnetic-field tilt on public CATS MHD simulations, and evaluates Claude answering questions about it through a read-only MCP server (`cats-tilt`).

## Deliverables

| File | What it is |
|---|---|
| `evaluation_protocol_alexis.md` | One-page protocol: metric, baseline, test dataset (with DOIs), uncertainty method and success threshold |
| `architecture_alexis.pdf` / `.png` | Pipeline from the CATS data through preprocessing, the MCP server and Claude to the evaluation step (`.dot` is the Graphviz source) |
| `bibliography_alexis.md` | Three annotated papers (about 150 words each, APA) |
| `baseline_alexis.ipynb` | The prior-only baseline, scored with the course harness: pass rate 10.0 % (95 % CI 2.8–30.1 %), angle MAE 16.8° (95 % CI 10.7–23.5°) |

## Supporting files

| File | What it is |
|---|---|
| `ground_truth_alexis.json` | The answer key: 20 items in the harness format |
| `physics_check_alexis.csv` | Estimator vs true tilt for each run, used in section 4 of the notebook |
| `make_ground_truth.py` | Builds both files from the simulation cubes by calling the analysis pipeline directly (no LLM, no MCP server) |
| `eval_harness.py` | The Meeting 1 harness with my `within_tolerance` rule (angles within 0.5°) |
| `score_desktop.py` | Scores Claude Desktop's answers, including checks that its verification IDs are genuine |
| `requirements.txt` | Python packages |

## Re-running the baseline

```
pip install -r requirements.txt
jupyter notebook baseline_alexis.ipynb      # then Kernel → Restart & Run All
```

The simulation cubes are not needed: without them the notebook uses the saved `ground_truth_alexis.json`. With the CATS cubes and `runs.json` in a project folder, `make_ground_truth.py --project <folder>` rebuilds the answer key (a few minutes to an hour per run).

## Connecting the MCP server to Claude Desktop

The server (`mcp_server_alexis.py`, with `verify.py` and its own README) is on the [`week7mcpserver`](https://github.com/bhalex86/ai-physics-alexis/tree/week7mcpserver) branch.

1. Install its requirements: `pip install -r requirements.txt`.
2. Open (or create) `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) and add:

   ```json
   {
     "mcpServers": {
       "cats-tilt": {
         "command": "/full/path/to/python3",
         "args": ["/full/path/to/mcp_server_alexis.py"],
         "env": {"CATS_PROJECT_DIR": "/full/path/to/project_folder"}
       }
     }
   }
   ```

   - `command` is the Python the requirements are installed in (`which python3`).
   - `CATS_PROJECT_DIR` is the folder holding `runs.json`, `results.db` and the run folders.
3. Quit Claude Desktop completely and reopen it. The `cats-tilt` tools then appear in the chat's tools menu.
4. Test it: *"List the runs, then estimate the tilt of b1p1_t900 viewed at 45°, and give the verification ID."*
5. Check that the answer came from the tools: `python verify.py <verification_id>` must print `VALID`.

No API key is needed for Claude Desktop. Do **not** give Claude Desktop a filesystem connector to the home folder: it could read the signing key and forge verification IDs.

## Scoring Claude (Week 9)

```
python score_desktop.py --make-sheet   # writes the question list and a blank answer sheet
# ask each question in a new Claude Desktop chat, 3 times, and fill in desktop_answers_alexis.csv
python score_desktop.py                # pass rate, angle MAE, fabrications, pass^3, ID checks
```

## Not in this repository (on purpose)

`planted_failures.json` (which stored value was corrupted), `audit_log.jsonl`, `.env` and `~/.cats_mcp_key` are private: they would let the agent, or anyone, see or forge the answers.
