# PHYS 7440 project (B. Alexis): reproduction targets.  `make help` lists them.
#
# From a fresh clone:
#     python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
#     make result
#
# PYTHON defaults to the python3 on your PATH (inside the venv, that is the venv's).

PYTHON ?= python3
RESULTS := results
RESULT_LOG := $(RESULTS)/key_result_run_alexis.jsonl
ATHDF := data/athena/Turb.out2.00050.athdf
export PYTHONHASHSEED := 0

.PHONY: review-checks rebuild help result check-reproducible trace pipeline pipeline-athena test sample clean-logs

help:
	@echo "make result              key result (figure + tables) from the committed cache, with its JSON log"
	@echo "make check-reproducible  run 'make result' twice and compare the outputs byte for byte"
	@echo "make trace               print the call tree of the committed result run from its log"
	@echo "make review-checks       recompute every number quoted in the review documents (~1 min)"
	@echo "make test                all offline tests"
	@echo "make pipeline            full rebuild: download 4 CATS runs (~1 GB) + Athena++ run, build, verify"
	@echo "make pipeline-athena     rebuild only the Athena++ run, in a scratch folder (no download)"
	@echo "make rebuild             recompute EVERYTHING with the HW10 method (needs runs.json, CATS + AREPO data)"
	@echo "make sample              synthetic sample project for the MCP server"

# The key result.  Step 1 checks the committed cache against the hash recorded in
# provenance_alexis.json (fails if the cache was edited); step 2 grades every run and
# draws the figure.  Both steps append to one JSON log, which is committed with the result.
result:
	@mkdir -p $(RESULTS)
	@rm -f $(RESULT_LOG)
	OBS_LOG_FILE=$(RESULT_LOG) $(PYTHON) data_pipeline_alexis.py --verify
	OBS_LOG_FILE=$(RESULT_LOG) $(PYTHON) make_key_result_alexis.py --out $(RESULTS)
	@echo "log: $(RESULT_LOG)   (view it with: make trace)"

check-reproducible:
	@rm -rf .repro && mkdir -p .repro/a .repro/b .repro/committed
	OBS_LOG_FILE=.repro/a.jsonl $(PYTHON) make_key_result_alexis.py --out .repro/a > /dev/null
	OBS_LOG_FILE=.repro/b.jsonl $(PYTHON) make_key_result_alexis.py --out .repro/b > /dev/null
	@echo "--- run 1 vs run 2"
	@$(PYTHON) make_key_result_alexis.py --compare .repro/a .repro/b
	@for f in $$(ls .repro/a); do git show HEAD:$(RESULTS)/$$f > .repro/committed/$$f; done
	@echo "--- this machine vs the committed results"
	@$(PYTHON) make_key_result_alexis.py --compare .repro/a .repro/committed
	@rm -rf .repro

review-checks:
	OBS_LOG_FILE=results/review_checks_run_alexis.jsonl $(PYTHON) review_checks_alexis.py

trace:
	$(PYTHON) trace_alexis.py $(RESULT_LOG)

test:
	ATHDF=$(ATHDF) $(PYTHON) -m pytest -q test_physics_alexis.py test_observability_alexis.py test_data_pipeline_alexis.py \
	  test_mcp_server_alexis.py test_evaluate.py

pipeline:
	$(PYTHON) data_pipeline_alexis.py --fetch --build --verify --all

rebuild:
	$(PYTHON) rebuild_alexis.py --plant-failures
	$(PYTHON) data_pipeline_alexis.py --build --verify --all

pipeline-athena:
	@rm -rf .scratch && mkdir -p .scratch && cp -r data .scratch/
	$(PYTHON) data_pipeline_alexis.py --project .scratch --build --source athena --no-github --verify
	@echo "log: .scratch/logs/pipeline_alexis.jsonl"

sample:
	$(PYTHON) make_sample_data.py

clean-logs:
	rm -rf logs .repro .scratch
