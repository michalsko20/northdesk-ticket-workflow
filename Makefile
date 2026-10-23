.PHONY: check test up down

check:
	uv run --locked ruff check .
	uv run --locked ruff format --check .
	uv run --locked mypy

test:
	docker compose --profile test build tests
	docker compose --profile test run --rm --no-deps tests

up:
	docker compose up --build -d --wait

down:
	docker compose down
