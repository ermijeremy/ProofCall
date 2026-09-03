SHELL := /bin/bash

PYTHON ?= .venv/bin/python
HOST ?= 0.0.0.0
PORT ?= 8766
CALLPROOF_URL ?= http://127.0.0.1:$(PORT)

.PHONY: help check-env init-db serve serve-local health teleexpert-health teleexpert-health-local probe-teleexpert preflight inspect test-active test-all routes

help:
	@echo 'Callwise real-validation commands:'
	@echo '  make check-env         Validate required .env values without printing secrets'
	@echo '  make init-db           Create/migrate the local database'
	@echo '  make serve             Start CallProof on all LAN interfaces'
	@echo '  make serve-local       Start CallProof using the TeleExpert URL from .env'
	@echo '  make health            Check the CallProof web server'
	@echo '  make teleexpert-health Check the real TeleExpert LAN gateway'
	@echo '  make teleexpert-health-local Check TeleExpert at 10.196.11.219:8080'
	@echo '  make probe-teleexpert   Send a harmless invalid request to verify POST reachability'
	@echo '  make preflight         Run both environment and connectivity checks'
	@echo '  make inspect           Inspect batches, calls, webhooks, and results (read-only)'
	@echo '  make test-active       Run Callwise integration tests without real calls'
	@echo '  make test-all          Run the complete repository test suite'
	@echo 'After preflight passes, open $(CALLPROOF_URL)/api/dashboard and submit the real call from the UI.'

check-env:
	@set -a; source .env 2>/dev/null || { echo 'Missing .env'; exit 1; }; set +a; \
	$(PYTHON) -c 'import os,sys; from urllib.parse import urlparse; required=("TELEXPERT_BASE_URL","TELEXPERT_API_KEY","TELEXPERT_WEBHOOK_URL","TELEXPERT_WEBHOOK_SECRET","CALLPROOF_DATABASE_URL"); missing=[key for key in required if not os.getenv(key)]; print("Missing: "+", ".join(missing) if missing else "Environment values present"); sys.exit(bool(missing)); base=os.environ["TELEXPERT_BASE_URL"]; hook=os.environ["TELEXPERT_WEBHOOK_URL"]; print("TeleExpert host:", urlparse(base).hostname); print("Webhook host:", urlparse(hook).hostname); print("Database:", os.environ["CALLPROOF_DATABASE_URL"])'

init-db:
	@set -a; source .env; set +a; $(PYTHON) -c 'from app.db.session import init_db; init_db(); print("Database initialized and upgraded")'

serve:
	@set -a; source .env; set +a; exec $(PYTHON) -m uvicorn app.main:app --host $(HOST) --port $(PORT)

serve-local:
	@set -a; source .env; set +a; exec $(PYTHON) -m uvicorn app.main:app --host $(HOST) --port $(PORT)

health:
	@curl --fail-with-body --connect-timeout 3 --max-time 10 -i $(CALLPROOF_URL)/health

teleexpert-health:
	@set -a; source .env; set +a; curl --fail-with-body --connect-timeout 5 --max-time 10 -i "$$TELEXPERT_BASE_URL/healthz"

teleexpert-health-local:
	@curl --fail-with-body --connect-timeout 5 --max-time 10 -i http://10.196.11.219:8080/healthz

probe-teleexpert:
	@set -a; source .env; set +a; \
	curl --connect-timeout 5 --max-time 10 -i -X POST "$$TELEXPERT_BASE_URL/v1/calls" \
	-H "Authorization: Bearer $$TELEXPERT_API_KEY" \
	-H 'Content-Type: application/json' \
	-d '{"phone_number":"000","prompt":"connectivity probe","response_format":"text"}'

preflight: check-env teleexpert-health
	@echo 'Preflight passed. Start CallProof with: make serve'
	@echo 'Then open: $(CALLPROOF_URL)/api/dashboard'
	@echo 'The expected TeleExpert health response must contain gateway_connected: true.'

inspect:
	@set -a; source .env; set +a; $(PYTHON) scripts/inspect_callwise.py $(ARGS)

test-active:
	@$(PYTHON) -m pytest -q \
		tests/unit/test_callwise_import.py \
		tests/unit/test_callwise_rules.py \
		tests/unit/test_callwise_questionnaire.py \
		tests/unit/test_reporting.py \
		tests/integration/test_callwise_pipeline.py \
		--disable-warnings

test-all:
	@$(PYTHON) -m pytest -q --disable-warnings

routes:
	@$(PYTHON) -c 'from app.main import app; print(chr(10).join(sorted(f"{sorted(r.methods)} {r.path}" for r in app.routes if getattr(r,"path","").startswith("/api"))))'
