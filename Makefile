.PHONY: test lint typecheck format

UV := uv run

test:
	$(UV) pytest apps/api/tests/unit -v

lint:
	$(UV) ruff check .
	$(UV) ruff format --check .

typecheck:
	$(UV) mypy apps packages

format:
	$(UV) ruff format .
