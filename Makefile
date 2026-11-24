# Convenience targets. Everything here is also a plain docker/npm/pip command.

.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: env
env: ## Create .env from the example if it does not exist
	@test -f .env || (cp .env.example .env && echo "created .env")

.PHONY: up
up: env ## Start the whole stack (sandbox, workload, backend, frontend)
	$(COMPOSE) up -d --build
	@echo ""
	@echo "  UI    http://localhost:3000"
	@echo "  API   http://localhost:8000/docs"
	@echo ""
	@echo "  First boot seeds ~1M rows into the sandbox; the Queries page stays"
	@echo "  empty until the workload generator has produced some traffic."

.PHONY: down
down: ## Stop the stack, keep the data
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop the stack and delete all volumes
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Follow backend logs
	$(COMPOSE) logs -f backend

.PHONY: logs-all
logs-all: ## Follow all logs
	$(COMPOSE) logs -f

.PHONY: ollama
ollama: ## Start Ollama and pull the default model
	$(COMPOSE) --profile ollama up -d ollama
	$(COMPOSE) exec ollama ollama pull qwen2.5-coder:14b

.PHONY: psql-sandbox
psql-sandbox: ## Open psql on the sandbox as the apply role
	$(COMPOSE) exec sandbox psql -U postgres -d shopdb

.PHONY: psql-meta
psql-meta: ## Open psql on the application's own database
	$(COMPOSE) exec meta psql -U dbopt -d dbopt_meta

.PHONY: reset-stats
reset-stats: ## Reset the sandbox's statistics, to re-test the heuristics cleanly
	$(COMPOSE) exec sandbox psql -U postgres -d shopdb \
		-c "SELECT pg_stat_reset(); SELECT pg_stat_statements_reset();"

.PHONY: test
test: ## Run the backend test suite
	cd backend && python -m pytest -q

.PHONY: lint
lint: ## Lint the backend and typecheck the frontend
	cd backend && python -m ruff check app tests
	cd frontend && npx tsc --noEmit

.PHONY: dev-backend
dev-backend: ## Run the backend locally with reload (needs the meta + sandbox containers)
	cd backend && uvicorn app.main:app --reload --port 8000

.PHONY: dev-frontend
dev-frontend: ## Run the frontend locally with reload
	cd frontend && npm run dev
