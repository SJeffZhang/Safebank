UV_CACHE_DIR ?= /private/tmp/sbg-uv-cache
export UV_CACHE_DIR
PYTHON := .venv/bin/python
.PHONY: setup keys test lint up down logs test-integration probe demo eval reset
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
up: keys wheels
	docker compose up -d --build --wait
down:
	docker compose down
logs:
	docker compose logs --tail=100
test-integration:
	$(PYTHON) -m scripts.network_acceptance
probe:
	$(PYTHON) -m scripts.deepseek_probe
# Do not return fake success before the business components exist.
demo eval reset:
	@echo '$@ is not implemented in P0; see PRD stages P1-P9.' >&2
	@exit 2
