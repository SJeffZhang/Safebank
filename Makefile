UV_CACHE_DIR ?= /private/tmp/sbg-uv-cache
export UV_CACHE_DIR
PYTHON := .venv/bin/python
LIMIT ?= 20
.PHONY: setup keys test lint wheels build up down logs test-integration probe demo eval reset \
 test-postgres-bootstrap contracts-check contracts-generate p1-provision migrate seed-p1 test-p1-build \
 test-p1-db test-p1-concurrency test-p1-consistency test-p1 gateway-provision migrate-gateway seed-demo test-obligations
setup:
	uv sync --frozen --group poc --python 3.12
keys:
	$(PYTHON) -m scripts.keys
test:
	.venv/bin/pytest -q
lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
wheels:
	$(PYTHON) -m scripts.download_wheels
build: keys wheels
	docker compose build bank-backend frontend
p1-provision:
	$(PYTHON) -m scripts.p1_database
gateway-provision:
	$(PYTHON) -m scripts.gateway_database
migrate:
	docker compose run --rm bank-maintenance
migrate-gateway:
	docker compose run --rm gateway-maintenance
seed-p1:
	docker compose run --rm bank-maintenance python -m app.bootstrap
seed-demo:
	docker compose run --rm bank-maintenance python -m app.seed_demo
up: build
	docker compose up -d --wait postgres-bank postgres-agent
	$(MAKE) p1-provision migrate seed-p1 gateway-provision migrate-gateway
	docker compose up -d --no-build --wait frontend operator agent gateway bank-backend
down:
	docker compose down
logs:
	docker compose logs --tail=100
test-integration:
	$(PYTHON) -m scripts.network_acceptance
	$(PYTHON) -m scripts.p1_http_acceptance
test-p1-build: build
	docker compose up -d --wait postgres-bank
	$(PYTHON) -m scripts.p1_database --fresh-test
	docker compose --profile test build p1-tests
	docker compose run --rm p1-tests python -m alembic -c services/bank-backend/alembic.ini upgrade head
	docker compose run --rm p1-tests python -m alembic -c services/bank-backend/alembic.ini upgrade head
test-p1-db:
	docker compose run --rm p1-tests python -m pytest tests/integration -q
test-p1-concurrency:
	docker compose run --rm p1-tests python -m pytest tests/integration -m concurrency -q
test-p1-consistency:
	docker compose run --rm p1-tests python -m pytest tests/integration -m consistency -q
test-p1: test-p1-build test-p1-db
probe:
	$(PYTHON) -m scripts.deepseek_probe
# Deliberately fail until later stages implement full demo/evaluation and baseline reset.
demo eval reset:
	@echo '$@ is not implemented in P1; see PRD stages P2-P9.' >&2
	@exit 2

contracts-check:
	PYTHONPATH=packages/contracts/src $(PYTHON) -m scripts.freeze_contracts --check

contracts-generate:
	PYTHONPATH=packages/contracts/src $(PYTHON) -m scripts.freeze_contracts

test-postgres-bootstrap:
	$(PYTHON) -m scripts.postgres_bootstrap_acceptance

test-gateway:
	.venv/bin/pytest tests/gateway --ignore=tests/gateway/db -q

test-gateway-db:
	GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/db -q

# W2 includes its real PostgreSQL ledger tests through the isolated fixture.
test-w2:
	GATEWAY_DB_TESTS=1 OBLIGATIONS_DB_TESTS=1 .venv/bin/pytest tests/gateway/w2 -q
.PHONY: test-w2

.PHONY: test-w1
test-w1:
	GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w1 -q

.PHONY: test-shared-runtime
test-shared-runtime:
	GATEWAY_DB_TESTS=1 GATEWAY_SECRET_TESTS=1 .venv/bin/pytest tests/gateway/shared_runtime -q -s

.PHONY: test-reserve
test-reserve:
	.venv/bin/pytest tests/bankcore/test_reserve.py -q

.PHONY: test-customer-locks
test-customer-locks:
	GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/customer_locks -q

.PHONY: test-wave1-state
test-wave1-state:
	GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/wave1_state -q

.PHONY: test-obligations
test-obligations:
	OBLIGATIONS_DB_TESTS=1 .venv/bin/pytest tests/obligations_data -q

.PHONY: test-w3a
test-w3a:
	GATEWAY_DB_TESTS=1 OBLIGATIONS_DB_TESTS=1 .venv/bin/pytest tests/gateway/w3a -q

.PHONY: test-w4b1
test-w4b1:
	GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w4b1 -q

.PHONY: test-agent-db
test-agent-db:
	AGENT_DB_TESTS=1 .venv/bin/pytest tests/agent -m db -q

.PHONY: test-w4b2b
test-w4b2b:
	GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w4b2b -q

test-w5-notify:
	GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w5 -q

.PHONY: test-replay
test-replay:
	.venv/bin/pytest tests/agent/test_replay.py -q

.PHONY: test-verify
test-verify:
	.venv/bin/pytest tests/agent/test_verify.py -q

.PHONY: test-cards
test-cards:
	.venv/bin/pytest tests/agent/test_cards.py -q

.PHONY: test-w4b2c
test-w4b2c:
	GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w4b2c -q

.PHONY: test-w5-sms
test-w5-sms:
	GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w5_sms -q

.PHONY: test-w3b
.PHONY: test-w3b
test-w3b:
	GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w3b -q

.PHONY: test-e0b
test-e0b:
	GATEWAY_DB_TESTS=1 OBLIGATIONS_DB_TESTS=1 .venv/bin/pytest tests/evaluation/runner -q

.PHONY: eval-l1
eval-l1:
	$(PYTHON) -m evaluation.runner.cli --llm-mode fake --limit $(LIMIT)

.PHONY: test-w4c
test-w4c:
	GATEWAY_DB_TESTS=1 OBLIGATIONS_DB_TESTS=1 .venv/bin/pytest tests/gateway/w4c -q

.PHONY: test-s1
test-s1:
	GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/s1 -q

.PHONY: try-agent
try-agent:
	docker compose exec operator python -m scripts.try_agent $(ARGS)

.PHONY: test-try-agent
test-try-agent:
	.venv/bin/pytest tests/try_agent -q

.PHONY: demo-up
demo-up:
	$(PYTHON) -m scripts.demo_environment up

.PHONY: demo-reset
demo-reset:
	$(PYTHON) -m scripts.demo_environment reset
