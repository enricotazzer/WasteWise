.PHONY: help setup up down logs migrate revision seed seed-recipes demo demo-clear reset test lint web-install web dev-https certs scan-stats

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

setup:  ## First-time setup: .env + build + migrate + seed
	@test -f .env || (cp .env.example .env && echo "created .env — edit FIRST_USER_PASSWORD and SECRET_KEY")
	docker compose build
	docker compose up -d db
	@echo "waiting for postgres..."
	@until docker compose exec -T db pg_isready -U wastewise >/dev/null 2>&1; do sleep 1; done
	docker compose up -d api
	$(MAKE) migrate
	$(MAKE) seed
	@echo ""
	@echo "  API   http://localhost:8000/docs"
	@echo "  Web   cd web && npm run dev  ->  http://localhost:5173"

up:     ## Start the stack
	docker compose up -d

down:   ## Stop the stack
	docker compose down

logs:   ## Tail API logs
	docker compose logs -f api

migrate:  ## Apply migrations
	docker compose exec -T api alembic upgrade head

revision:  ## Autogenerate a migration: make revision m="add x"
	docker compose exec -T api alembic revision --autogenerate -m "$(m)"

seed:   ## Load categories, shelf-life table, products, first user
	docker compose exec -T api python -m app.seeds.load

demo:   ## Fill the app with a synthetic 8-week household (flagged is_demo, not training data)
	docker compose exec -T api python -m app.seeds.demo

demo-clear:  ## Remove every synthetic row, leaving real logging untouched
	docker compose exec -T api python -m app.seeds.demo --clear

reset:  ## DESTRUCTIVE: drop the database volume and rebuild from scratch
	docker compose down -v
	$(MAKE) setup

test:   ## Run the test suite inside the api container
	docker compose exec -T api pytest -q

lint:   ## Ruff check + format
	docker compose exec -T api ruff check app tests
	docker compose exec -T api ruff format --check app tests

web-install:  ## Install frontend deps
	cd web && npm install

web:    ## Run the frontend dev server (localhost only)
	cd web && npm run dev

certs:  ## Issue local TLS certs for this machine's LAN IP (needs: brew install mkcert)
	@command -v mkcert >/dev/null || { \
	  echo "mkcert is not installed. Run:  brew install mkcert && mkcert -install"; exit 1; }
	@mkdir -p web/.certs
	@IP=$$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $$1}'); \
	 test -n "$$IP" || { echo "could not determine LAN IP"; exit 1; }; \
	 cd web/.certs && mkcert -key-file key.pem -cert-file cert.pem "$$IP" localhost 127.0.0.1 ::1; \
	 echo ""; echo "  Open on your phone:  https://$$IP:5173"

dev-https:  ## Serve the frontend over HTTPS on the LAN — REQUIRED for the phone camera
	@# getUserMedia refuses on a plain-http LAN address, silently. Without TLS the scanner
	@# simply never opens the camera on a phone, which is the only device it's for.
	@test -f web/.certs/cert.pem || $(MAKE) certs
	@IP=$$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $$1}'); \
	 echo "  Phone:  https://$$IP:5173"
	cd web && npm run dev

scan-stats:  ## Module 1 scorecard: how many barcodes resolved without typing
	@docker compose exec -T db psql -U wastewise -d wastewise -c \
	  "SELECT match_method, count(*) AS barcodes, sum(lookup_count) AS scans \
	   FROM product_barcodes GROUP BY match_method ORDER BY scans DESC;"

seed-recipes:  ## Load the starter Italian recipe corpus; reports its ingredient match rate
	docker compose exec -T api python -m app.seeds.load_recipes
