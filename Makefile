.PHONY: dev test lint build openapi
dev:
	docker compose up --build
test:
	cd backend && uv run --extra dev pytest
	cd extension && npm test
lint:
	cd backend && uv run --extra dev ruff check src tests
	cd extension && npm run lint
build:
	cd extension && npm ci && npm run build
openapi:
	cd backend && uv run python -m sloppy_checker.export_openapi

