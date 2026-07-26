.DEFAULT_GOAL := help

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

$(VENV)/bin/activate:
	python3 -m venv $(VENV)

venv: $(VENV)/bin/activate ## Create the local virtualenv (.venv)

install: venv ## Install dev dependencies into .venv
	$(PIP) install -r requirements-dev.txt

install-e2e: venv ## Install e2e dependencies (Playwright) into .venv
	$(PIP) install -r requirements-e2e.txt
	$(VENV)/bin/playwright install chromium

lint: ## Run ruff
	$(VENV)/bin/ruff check .

test: ## Run pytest with coverage (same gate as CI, fail_under=80 in pyproject.toml)
	$(VENV)/bin/pytest --cov=app --cov-report=term-missing

e2e: ## Run Playwright e2e tests (needs install-e2e first)
	$(VENV)/bin/pytest e2e/ -v

run: ## Start a local dev instance with auto-reload (SQLite in ./data)
	RAREBIRDALERT_DB_PATH=./data/rarebirdalert.db SESSION_SECRET_KEY=dev-only \
		$(VENV)/bin/uvicorn app.main:app --reload --port 8000

docker-up: ## Build and start the container via docker compose (production-like)
	docker compose up -d --build

docker-down: ## Stop the docker compose stack
	docker compose down

clean: ## Remove the virtualenv and local caches
	rm -rf $(VENV) .pytest_cache .ruff_cache .coverage

help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.PHONY: venv install install-e2e lint test e2e run docker-up docker-down clean help
