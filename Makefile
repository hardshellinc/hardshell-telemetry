# Developer helpers. `make precommit` mirrors the checks CI runs on a PR, so a
# green run here means a green PR.
.PHONY: help precommit lint format typecheck test

help:
	@echo "make precommit  - lint, format-check, type-check, and test (run before committing)"
	@echo "make lint       - ruff check"
	@echo "make format     - ruff format --check"
	@echo "make typecheck  - ty check"
	@echo "make test       - pytest"

lint:
	uv run ruff check .

format:
	uv run ruff format --check .

typecheck:
	uv run ty check

test:
	uv run pytest

precommit: lint format typecheck test
	@echo "precommit: all checks passed"
