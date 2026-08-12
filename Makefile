# SkyNYC — operational interface for the whole stack: infrastructure, ingestion,
# streaming, batch, dbt, and the lakehouse targets (PRD §11).
SHELL := /bin/bash
COMPOSE := docker compose
KAFKA_BIN := /opt/kafka/bin
KAFKA_EXEC := $(COMPOSE) exec kafka $(KAFKA_BIN)
BOOTSTRAP := localhost:9092
PG_USER ?= skynyc
PG_DB ?= skynyc

.DEFAULT_GOAL := help
.PHONY: help up down logs ps topics smoke psql ingest lag credits test stream batch dbt-build backup lake-backfill

help:
	@echo "SkyNYC targets:"
	@echo "  up      start kafka, postgres, grafana (schema auto-applies on first boot)"
	@echo "  down    stop all, KEEP volumes"
	@echo "  logs    follow logs for all services"
	@echo "  ps      service state + health"
	@echo "  topics  create the three topics with PRD §6 retention (idempotent)"
	@echo "  smoke   OpenSky token + one authed /states/all + one KJFK observation"
	@echo "  psql    interactive psql on the skynyc database"
	@echo "  ingest  build + start both producers and the weather consumer"
	@echo "  lag     weather consumer group lag (Spark never appears here — Manual 04)"
	@echo "  credits last logged OpenSky credit balance + daily projection"
	@echo "  test    run the pytest suite (parsers now; detectors from M3)"
	@echo "  stream  build + start the Spark streaming app (bronze + live + events)"
	@echo "  batch   build + start dagster webserver + daemon (UI on :3001)"
	@echo "  dbt-build  dbt deps + build (staging, marts, tests) inside the dagster image"
	@echo "  backup  run the postgres_backup asset once (pg_dump -Fc -> Azure backups)"
	@echo "  lake-backfill  land the full BTS/IEM history via ADF (one-time, ~470 files)"
	@echo "  lake-run       run the Databricks medallion build + gold->PG via Dagster"

up:
	$(COMPOSE) up -d
	@echo "grafana: http://localhost:3000 (admin/admin)"

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

# Retention per PRD §6: states 7d, weather 30d. Safe to re-run.
topics:
	$(KAFKA_EXEC)/kafka-topics.sh --bootstrap-server $(BOOTSTRAP) --create --if-not-exists \
	  --topic adsb.states.raw --partitions 3 --config retention.ms=604800000
	$(KAFKA_EXEC)/kafka-topics.sh --bootstrap-server $(BOOTSTRAP) --create --if-not-exists \
	  --topic wx.observations --partitions 1 --config retention.ms=2592000000
	$(KAFKA_EXEC)/kafka-topics.sh --bootstrap-server $(BOOTSTRAP) --create --if-not-exists \
	  --topic wx.alerts --partitions 1 --config retention.ms=2592000000
	@$(KAFKA_EXEC)/kafka-topics.sh --bootstrap-server $(BOOTSTRAP) --list

# Costs 1 states credit of the 4,000/day budget. Needs no running containers.
smoke:
	python3 scripts/smoke.py

psql:
	$(COMPOSE) exec postgres psql -U $(PG_USER) -d $(PG_DB)

# Build once, then start: the three services share one image tag, and letting
# `up --build` build it three times in parallel races on the tag.
ingest:
	$(COMPOSE) build producer-opensky
	$(COMPOSE) up -d producer-opensky producer-weather consumer-weather

# Spark tracks offsets in its checkpoint, not a consumer group, so this shows
# ONLY the weather consumer by design (Manual 04).
lag:
	$(KAFKA_EXEC)/kafka-consumer-groups.sh --bootstrap-server $(BOOTSTRAP) \
	  --describe --group skynyc-weather

# Reads the greppable credits_remaining= field the producer logs on every poll.
credits:
	@last=$$($(COMPOSE) logs --no-log-prefix producer-opensky 2>/dev/null \
	  | grep -oE 'credits_remaining=[0-9]+' | tail -1 | cut -d= -f2); \
	if [ -z "$$last" ]; then echo "no credit header logged yet — is producer-opensky running?"; exit 1; fi; \
	echo "states credits remaining: $$last / 4000"; \
	echo "projected daily burn at 30s polling: 2880 (budget <= 3000 — PRD §2.3)"

test:
	python3 -m pytest tests/ -q

stream:
	$(COMPOSE) build spark
	$(COMPOSE) up -d spark

batch:
	$(COMPOSE) build dagster-webserver
	$(COMPOSE) up -d dagster-webserver dagster-daemon
	@echo "dagster UI: http://localhost:3001 (schedules ship OFF — toggle under Automation)"

dbt-build:
	$(COMPOSE) exec -T dagster-webserver sh -c \
	  "cd /app/dbt/skynyc_dbt && dbt deps --profiles-dir . -q && dbt build --profiles-dir ."

# One-off manual backup; the nightly_backup schedule covers 03:30 ET once it is
# toggled on in the UI (Manual 07).
backup:
	$(COMPOSE) exec -T dagster-webserver dagster asset materialize \
	  -m skynyc_dagster.definitions --select postgres_backup

# One-time full-history landing (PRD §13 M4A). Generates the month list up to
# the freshest published month (~2-month reporting lag) and hands both ADF
# pipelines their parameters. Idempotent: re-landing overwrites the same paths.
lake-backfill:
	python3 scripts/gen_backfill_params.py $$(date -d '3 months ago' +%Y 2>/dev/null || date -v-3m +%Y) $$(date -d '3 months ago' +%-m 2>/dev/null || date -v-3m +%-m) /tmp/skynyc-backfill
	az datafactory pipeline create-run -g skynyc-rg --factory-name skynyc-adf \
	  --name pl_land_bts --parameters @/tmp/skynyc-backfill/bts_backfill.json
	az datafactory pipeline create-run -g skynyc-rg --factory-name skynyc-adf \
	  --name pl_land_iem --parameters @/tmp/skynyc-backfill/iem_backfill.json

# Full medallion build + gold->PG upsert, through Dagster (the control plane).
lake-run:
	$(COMPOSE) exec -T dagster-webserver dagster asset materialize \
	  -m skynyc_dagster.definitions --select databricks_medallion_run,gold_airport_day_delay_pg
