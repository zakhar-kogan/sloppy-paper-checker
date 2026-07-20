.PHONY: dev test lint build openapi
dev:
	docker compose up --build
test:
	cd backend && uv run --extra dev pytest
	cd web && npm test
lint:
	cd backend && uv run --extra dev ruff check src tests
	cd web && npm run lint
build:
	cd web && npm ci && npm run build
openapi:
	cd backend && uv run python -m sloppy_checker.export_openapi
	cd web && npm run generate:types
