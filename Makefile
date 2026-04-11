.PHONY: fix check test ci-check install-hooks clean

fix:
	poetry run ruff check --fix src/ tests/
	poetry run ruff format src/ tests/

check:
	poetry run ruff check src/ tests/

test:
	poetry run pytest tests/ -v

ci-check: check test

install-hooks:
	poetry run pre-commit install

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
	rm -rf .pytest_cache .ruff_cache dist
