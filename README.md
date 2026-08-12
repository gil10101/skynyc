# SkyNYC

[![CI](https://github.com/gil10101/skynyc/actions/workflows/ci.yml/badge.svg)](https://github.com/gil10101/skynyc/actions/workflows/ci.yml)

Real-time measurement of how weather degrades airport operations in the New York
terminal area — JFK, LaGuardia, and Newark.

The pipeline ingests live aircraft positions (OpenSky Network ADS-B) and official
weather observations and alerts (NOAA / National Weather Service), derives
operational events — **arrivals, holding patterns, go-arounds** — from raw
telemetry via stateful stream processing, validates the derived events against an
independent ground-truth source with a published precision/recall score, and
serves both a live dashboard and an analytical answer.

**The business question:** how much do arrival rates fall — and holding and
go-arounds rise — as conditions degrade from VFR to IFR, and which weather
variable (visibility, ceiling, wind/gusts) is the strongest leading indicator?

**What makes it interesting:** the operational events exist in no feed. They are
*derived* from position telemetry by per-aircraft state machines, then *validated*
daily against ground truth. The derive-and-validate loop — not the moving map —
is the point.

---

## The system, live

![Live dashboard: aircraft over the NYC terminal area, per-station conditions, pipeline freshness, 24 hours of detected arrivals with NWS alert overlays, and alert state per airport](assets/img/grafana-live-dashboard.png)
*The provisioned dashboard over a full day: heading-rotated aircraft colored by altitude, per-station flight category, the freshness stat doubling as the liveness alarm, detected arrivals per 15 minutes across all three airports — overnight lull and evening push visible — with NWS alert windows overlaid as region annotations, and the alert state timeline.*

| | |
|---|---|
| ![Spark streaming query statistics: input rate, process rate, batch durations over hundreds of batches](assets/img/spark-live-query-stats.png) *The live query's statistics: rates, rows, and batch durations across hundreds of micro-batches* | ![Dagster run detail: ground-truth pull with per-airport arrival counts and credit accounting in the event log](assets/img/dagster-gt-run.png) *A ground-truth run: 1,592 arrivals pulled, per-airport counts and API-credit accounting in the log* |
| ![Derived flight events queried live from Postgres](assets/img/events-live.png) *The product no feed provides: derived arrivals and holding patterns* | ![Azure ADLS Gen2 bronze archive listing](assets/img/azure-lake.png) *The lake: partitioned Parquet archive plus database backups* |

![Dagster global asset lineage with the quality mart selected: raw tables through staging views, spine and hourly weather, facts and marts, plus the asset's raw SQL, data versions, and per-input event pointers in the detail pane](assets/img/dagster-global-asset-lineage.png)
*The analytical DAG, discovered from the dbt manifest: raw tables (dashed, fed by
the stream outside Dagster) through staging views, the airport-hour spine and
last-known-value hourly weather, into `fct_airport_hourly`, event-time-enriched
events, and the impact and quality marts — every node freshly materialized by the
hourly schedule with its test counts green. The detail pane carries the model's
raw SQL and per-input data versions. The grain of every model is a one-sentence
contract enforced by a uniqueness test.*

### The historical lakehouse

38 years of BTS on-time performance (181M rows after dedupe) and 4.3M METAR observations,
landed by Data Factory and built into a bronze/silver/gold Delta medallion by a Databricks
job that Dagster triggers over the Jobs API. Postgres stays the only serving store; a
serverless SQL warehouse over Unity Catalog external tables is the analyst's window into
the history. BTS serves no PREZIP files for 1990-1999 (verified against both naming
schemes) — the gap is landed around, documented, and self-heals if the source restores it.

Deliberately single-node compute: bronze is per-archive by construction (ZIPs are not
splittable), and the subscription's SKU allow-list and 4-vCPU family quota cap the cluster
at one machine — worker count is a single Terraform variable when the ceiling lifts. The
build consumed about $10 of a $200 credit grant.

![Databricks multi-task run: bronze, silver, gold all green with durations](assets/img/databricks-run-dag.png)
*The medallion build: 344 monthly archives to bronze in 2h58m, the 181M-row
typed dedupe in 31m, weather parse and gold marts in minutes. Databricks labels Jobs-API
runs "Manually" — the caller is the Dagster asset; lineage is blank by design because the
jobs write governed paths, not catalog tables.*

| | |
|---|---|
| ![Bronze ingestion driver log: per-month row counts across 344 archives](assets/img/databricks-bronze-ingest.png) *Bronze's receipt: every month logged with row counts, 181,076,399 rows across the span* | ![Spark UI mid-silver: 200-task stage, 6.2 GiB shuffle read](assets/img/databricks-spark-ui.png) *Silver's dedupe is the distributed workload: a 200-task Delta write over 6.2 GiB of shuffle* |
| ![SQL editor: arrival delay by flight category over 38 years, sub-second on serverless](assets/img/databricks-sql-thesis.png) *The thesis in one query, 0.8s on the serverless warehouse: average arrival delay runs 8-14x higher on LIFR days than VFR days at all three airports* | ![Unity Catalog: external Delta tables over the lake with full schema](assets/img/databricks-catalog.png) *The governed window: external tables over the same Delta paths the jobs write — the warehouse's only route to the lake* |

![Analytics dashboard: arrival delay staircase by flight category per airport, weather vs NAS delay by year, 38-year cancellation seasonality, and the worst weather days on record](assets/img/skynyc-analytics.png)
*Delay climbing VFR to LIFR at every airport, two decades
of cause-attributed delay with the 2006-07 peak and the COVID collapse, winter
and convective-summer cancellation seasonality, and a worst-days table that reads
as a storm almanac — Sandy, the 2018 blizzards, February 2026.*

---

## Architecture

```
 OpenSky /states/all ──┐                          ┌─▶ Parquet bronze ──▶ Azure ADLS Gen2
   poll 30s, credit    │    ┌──────────────┐      │   (dt=/hr=, immutable archive)
   guarded             ├───▶│ Kafka (KRaft) │──────┤
 NWS obs + alerts ─────┘    │ 3 topics      │      ├─▶ live_states  ──▶ Postgres ──▶ Grafana
   5min / 2min, unit-       │ 7d/30d        │      │   (10s upsert)                 (map, panels)
   code parsing             └──────┬────────┘      └─▶ flight_events
                                   │            Spark Structured Streaming
                                   │            (3 queries, checkpointed)
                            plain consumer ──▶ Postgres (weather_obs / weather_alerts)

 OpenSky /flights/arrival ──▶ Dagster (daily 09:15 ET) ──▶ arrivals_ground_truth
                                                            │
                              dbt: staging ──▶ marts ◀──────┘
                              (detection quality: precision / recall per day per airport)
```

| Component | Role | Scoping |
|---|---|---|
| Python producers ×2 | Poll REST APIs, wrap in a typed envelope, publish to Kafka; own the OAuth2 token lifecycle and the API credit budget | Polling→Kafka is the standard bridge for poll-only sources |
| Kafka (single node, KRaft) | Durable buffer and 7-day replay log | Not here for throughput (~5 msg/s) — here because the upstream API serves at most 1 h of history, so the retained log is the only replay substrate detector tuning has |
| Spark Structured Streaming | Bronze archive, live-position upserts, and stateful per-aircraft event detection (`applyInPandasWithState`) | Single-node `local[*]` by design; the code is cluster-portable |
| Plain Python consumer | Weather topics → Postgres upserts | 3 messages per 5 minutes does not need distributed compute |
| Azure ADLS Gen2 | The lake: immutable bronze archive + nightly backups | Durability the droplet cannot provide; compute is disposable, storage is not |
| DigitalOcean droplet | The compute host: all nine containers, 24/7 | Flat always-on workload; burst-priced clouds would charge a premium for a profile this steady |
| Postgres 16 | Serving layer and warehouse | |
| dbt | Staging → marts; tests and source freshness double as pipeline monitoring | |
| Dagster | Batch only: daily ground-truth pull, scheduled builds | Streams are supervised by Docker restart policies — an orchestrator's run model is the wrong shape for an infinite process |
| Grafana OSS | Live geomap + operational panels, provisioned from files | The repo is the dashboard; UI edits are exported back or they die |

## Deployment topology

The split that matters: **compute is disposable, storage is not.** Everything
that computes runs on one rented box; everything that must survive that box
lives in object storage.

```
 Laptop (dev only)                DigitalOcean droplet, NYC3          Azure (eastus)
 ------------------               ---------------------------        --------------------
 edit, test, commit    deploy.sh  9 containers via compose:          skynycbronzegil
 pytest + ruff        ---------->   kafka, postgres, grafana,        (StorageV2 + HNS, LRS)
 SSH tunnels for UIs    (rsync)     spark, 2 producers, consumer,      bronze/  Parquet archive
                                    dagster webserver + daemon         backups/ nightly pg_dump
```

**Compute — DigitalOcean droplet** (Basic tier, Ubuntu 24.04, Docker Compose):
started at 4 GB for the ingestion phase, resized in place to 8 GB the day the
Spark driver (4 GB heap) deployed — droplet resizes preserve the disk, so the
upgrade cost about a minute of downtime and the restart policies brought the
stack back unaided. Current steady-state: about 4.7 GB RAM in use across the
nine containers, Spark owning half of it. Kafka data, Postgres data, and Spark
checkpoints live on the droplet's disk in named volumes: they are the working
set, not the archive, and losing them costs at most the Kafka retention window
of re-derivable state.

**Storage — Azure ADLS Gen2** (StorageV2 with hierarchical namespace, LRS,
public access disabled, TLS 1.2 floor): the bronze Parquet archive Spark writes
via `abfss://`, and the nightly `pg_dump` the batch layer uploads. A lifecycle
rule tiers archive blobs to Cool at 30 days. This is the system of record —
the upstream API serves at most one hour of history, so if the archive is lost,
it is lost. Provisioned by `scripts/provision_azure.sh`.

Why this pairing instead of one cloud for both: an 8 GB droplet is a fraction
of the price of the equivalent on-demand EC2 instance, and this workload has
no burst profile that would reward per-second billing.
Object storage is the piece where a hyperscaler earns its place — durability a
single droplet cannot approach — so the lake goes there and nothing else does. Managed Kafka and cloud warehouses stay out: at 5 msg/s and
a few thousand events a day they would be cost without engineering content.

**Security posture:** every published container port binds `127.0.0.1` — not
as a preference but because Docker programs its own iptables rules ahead of
host firewalls, so a bare `9092:9092` on a public droplet is internet-exposed
no matter what ufw says. Reaching Grafana (3000), Dagster (3001), or the Spark
UI (4040) means an SSH tunnel. The host allows SSH only, key-only
authentication, unattended security upgrades on. Secrets live in `.env`, which
is gitignored and travels only over the deploy channel.

**Operations:** `scripts/deploy.sh` is the whole deploy story — rsync the
working tree, install Docker and swap on a fresh host, apply numbered
migrations idempotently, start the stack, verify. Re-running it is the update
path. The server is not a place where code is edited.

## Event detection

Per-aircraft state machines over a ring buffer of recent position samples,
keyed by transponder address (Kafka partitioning guarantees per-aircraft
ordering):

- **Arrival** — approach candidacy (near field, low, descending) confirmed by
  ground contact — or by *coverage loss on short final*, a verified real-world
  pattern: transponder contact commonly drops below ~450 m on approach.
  Confirmed via a 90-second state timeout.
- **Holding** — sustained turning (≥ 340° cumulative unwrapped track change
  within 8 minutes) without net displacement, inside the holding altitude/speed
  band, filtered to airline-class traffic.
- **Go-around** — descent on approach followed by a committed climb (two
  consecutive strong-climb samples and ≥ 200 m regained) with no touchdown.

Every threshold is a named constant; tuning happens by **replaying the retained
Kafka log** with adjusted values and re-scoring against ground truth — the
scores land in `mart_detection_quality` (grain: day × airport), targets
P ≥ 85% / R ≥ 80%.

First live scores, from the first scored day (matching rule: same transponder,
same airport, |Δt| ≤ 10 min against OpenSky's independent arrival records):

| Airport | Precision | Recall |
|---|---|---|
| JFK | 94.4% | 98.3% |
| LaGuardia | 93.3% | 94.1% |
| Newark | 94.6% | 85.2% |

Precision is measured against the full ground-truth day. The recall figures
above were measured over the window the detection stream was actually running —
it started mid-day on the first scored day, and counting hours it never saw
would report detector misses that are actually absence. The mart enforces both
rules structurally: precision publishes only once the day's ground truth has
landed, and recall publishes only for days the detector observed at least 20
of 24 hours (`observed_hours` is a column, so the gate is auditable). An
unscored day and a bad day must never look alike.

Detectors have no Spark dependency; the fixture suite runs the
exact production logic over **recorded live sequences** (a full BA descent into
JFK, a Delta flight vanishing at 83 m on LaGuardia final, overflights, taxi
traffic) plus disclosed synthetic geometry for patterns weather hasn't provided
yet.

## Project status

| Phase | Scope | Status |
|---|---|---|
| M0 Foundations | Compose stack, schema, topics, live smoke test | Complete |
| M1 Ingestion | Producers with credit guard + unit-aware parsing, weather consumer, parser suite over recorded fixtures | Complete |
| M2 Live map | Spark bronze→Azure + live positions, provisioned dashboard | Complete |
| M3 Detection & validation | Detectors + fixture suite, ground-truth pull, quality mart | Scored: precision 93-95% across all three airports, recall 85-98% over the detection window |
| M4 Modeling | Full dbt DAG (hourly facts, weather join-at-read, impact mart), scheduled builds | Built and self-running: hourly build + source freshness on the Dagster daemon, daily quality report after the ground-truth pull |
| M5 Dashboard & soak | Remaining panels, alert overlays, 7-day continuous run | Panels live with alert annotations; soak underway |
| M6 Analysis & packaging | The written answer, demo capture | — |

## Running it

Prerequisites and full walkthroughs live in `docs/` (PDF operator guides:
setup, operations, cloud deployment). The short version:

```bash
cp .env.example .env        # fill: OpenSky client id/secret, contact email
make up                     # kafka, postgres, grafana (+ schema on first boot)
make topics                 # three topics, correct retention
make smoke                  # live checks against both APIs — no mocks
make ingest                 # producers + weather consumer
make stream                 # spark: bronze + live + events
make batch                  # dagster (toggle the schedule on once, in the UI)
make dbt-build              # staging + quality mart, with tests
```

`make help` lists the operational targets (`credits`, `lag`, `psql`, `test`).

Deploying to a server: `scripts/deploy.sh` (any Ubuntu host, root or sudo user).
Azure lake provisioning: `scripts/provision_azure.sh`.

## Design decisions worth defending

- **Kafka at 5 msg/s** — replay is the feature. The API keeps one hour of
  history; the retained log is the only thing that makes threshold tuning
  possible at all.
- **Join-at-read, not stream-stream** — weather changes a few times an hour;
  events are enriched at query time with the observation valid at event time.
  A stream-stream join here would be architecture theater.
- **Docker supervises streams, Dagster supervises batch** — infinite processes
  are not jobs.
- **Units are load-bearing** — every NWS quantity is converted by reading its
  `unitCode` (wind arrives in km/h), never inferred from a field name.
- **Nulls are data** — position-less rows are counted and dropped at the bronze
  parse; on-ground aircraft legitimately report null altitude; coverage gaps
  are recorded as absence, never interpolated.
- **Idempotency everywhere** — deterministic event ids, upserts on natural
  keys: replays and crash-recovery cannot duplicate rows.
- **Loopback-bound ports** — Docker's iptables rules bypass host firewalls, so
  every published port binds `127.0.0.1`; remote access is an SSH tunnel.

## Data sources

- **OpenSky Network** — aircraft state vectors and arrival ground truth, used
  under their research/non-commercial terms.
  Bringing up OpenSky in publications: M. Schäfer, M. Strohmeier, V. Lenders,
  I. Martinovic, M. Wilhelm. *Bringing Up OpenSky: A Large-scale ADS-B Sensor
  Network for Research*. IPSN 2014.
- **NOAA / National Weather Service** — station observations and active
  alerts; US-government open data. Requests carry the required identifying
  `User-Agent`.

Infrastructure cost: one small VPS (~$24–48/mo) plus minimal object storage.

## Repository layout

```
producers/       OpenSky + NWS pollers, shared token/backoff/parsing/models
consumers/       weather → Postgres upserts
streaming/       Spark app (bronze / live / events) + detectors + sinks
orchestration/   Dagster: daily ground-truth asset, schedules
dbt/skynyc_dbt/  staging, spine + hourly-weather intermediates, fact and impact/quality marts
db/init/         first-boot schema + numbered migrations
grafana/         provisioned datasource + dashboard (the repo is the dashboard)
scripts/         smoke test, fixture capture, Azure provisioning, VPS deploy
tests/           parser + detector suites over recorded live fixtures
docs/            operator guides (PDF): setup, operations, cloud
```
