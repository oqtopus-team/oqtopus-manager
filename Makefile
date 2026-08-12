SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

.PHONY: install run format lint test verify docs-lint docs-build docs-serve help

install: ## Install dependencies, commit template and OQtopus CLI
	@uv sync --all-groups
	@if [ -d .git ]; then \
		git config --local commit.template .gitmessage; \
	fi
	@if [ ! -f config/config.yaml ]; then \
		cp config/config.yaml.example config/config.yaml; \
		echo "Created config/config.yaml from config/config.yaml.example"; \
	fi
	@curl -LsSf https://raw.githubusercontent.com/oqtopus-team/oqtopus-cli/main/scripts/install.sh | sh

run: ## Run the application
	@uv run python -m oqtopus_manager.main -c config/config.yaml -l config/logging.yaml

format: ## Run code formatting
	@uv run ruff check --fix
	@uv run ruff format

lint: ## Run linting
	@uv lock --check
	@uv run ruff check
	@uv run ruff format --check
	@uv run mypy

test: ## Run tests
	@uv run pytest

verify: format lint test ## Run all verification steps (formatting, linting, testing)

docs-lint: ## Run documentation linting
	@uv run pymarkdownlnt scan -r docs

docs-build: ## Build documentation
	@uv run mkdocs build

docs-serve: ## Serve documentation locally
	@uv run mkdocs serve

help: ## Show help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Available targets:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(filter-out .env,$(MAKEFILE_LIST)) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
