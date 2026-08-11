"""Weather topics -> Postgres upserts (PRD §4: "plain consumer", ~3 msg/5 min).

Consumer group `skynyc-weather` — the ONLY group that appears in
kafka-consumer-groups output; Spark tracks its own offsets in checkpoints
(Manual 04). Offsets are committed only after a successful upsert, so a crash
between write and commit replays the message into an idempotent ON CONFLICT
update — no loss, no duplicates (ground rule 7).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time

import psycopg
from confluent_kafka import Consumer, KafkaError

log = logging.getLogger("weather_to_pg")

OBS_TOPIC = "wx.observations"
ALERTS_TOPIC = "wx.alerts"
GROUP_ID = "skynyc-weather"

UPSERT_OBS = """
INSERT INTO weather_obs (station, obs_ts, temp_c, wind_speed_kmh, wind_gust_kmh,
                         wind_dir_deg, visibility_m, ceiling_ft, precip_last_hr_mm,
                         text_desc, flight_category, raw)
VALUES (%(station)s, %(obs_ts)s, %(temp_c)s, %(wind_speed_kmh)s, %(wind_gust_kmh)s,
        %(wind_dir_deg)s, %(visibility_m)s, %(ceiling_ft)s, %(precip_last_hr_mm)s,
        %(text_desc)s, %(flight_category)s, %(raw)s)
ON CONFLICT (station, obs_ts) DO UPDATE SET
  temp_c = EXCLUDED.temp_c, wind_speed_kmh = EXCLUDED.wind_speed_kmh,
  wind_gust_kmh = EXCLUDED.wind_gust_kmh, wind_dir_deg = EXCLUDED.wind_dir_deg,
  visibility_m = EXCLUDED.visibility_m, ceiling_ft = EXCLUDED.ceiling_ft,
  precip_last_hr_mm = EXCLUDED.precip_last_hr_mm, text_desc = EXCLUDED.text_desc,
  flight_category = EXCLUDED.flight_category, raw = EXCLUDED.raw
"""

UPSERT_ALERT = """
INSERT INTO weather_alerts (alert_id, airport, event, severity, effective_ts, ends_ts, raw)
VALUES (%(alert_id)s, %(airport)s, %(event)s, %(severity)s, %(effective_ts)s, %(ends_ts)s, %(raw)s)
ON CONFLICT (alert_id, airport) DO UPDATE SET
  event = EXCLUDED.event, severity = EXCLUDED.severity,
  effective_ts = EXCLUDED.effective_ts, ends_ts = EXCLUDED.ends_ts, raw = EXCLUDED.raw
"""

_running = True


def _stop(signum: int, _frame: object) -> None:
    global _running
    log.info("signal %s — exiting", signum)
    _running = False


def upsert(connection: psycopg.Connection, topic: str, payload: dict) -> None:
    params = dict(payload)
    params["raw"] = json.dumps(params.get("raw") or {})
    with connection.cursor() as cursor:
        cursor.execute(UPSERT_OBS if topic == OBS_TOPIC else UPSERT_ALERT, params)
    connection.commit()


def connect_pg(dsn: str) -> psycopg.Connection:
    while True:
        try:
            connection = psycopg.connect(dsn)
            log.info("connected to postgres")
            return connection
        except psycopg.OperationalError as exc:
            log.warning("postgres unavailable (%s) — retrying in 10s", exc)
            time.sleep(10)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    consumer = Consumer(
        {
            "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092"),
            "group.id": GROUP_ID,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,  # commit only after the upsert lands
        }
    )
    consumer.subscribe([OBS_TOPIC, ALERTS_TOPIC])
    connection = connect_pg(os.environ["PG_DSN"])
    log.info("consuming %s + %s as group %s", OBS_TOPIC, ALERTS_TOPIC, GROUP_ID)

    while _running:
        message = consumer.poll(1.0)
        if message is None:
            continue
        if message.error():
            if message.error().code() != KafkaError._PARTITION_EOF:
                log.warning("kafka error: %s", message.error())
            continue
        try:
            payload = json.loads(message.value())
        except (ValueError, TypeError):
            log.warning("undecodable message on %s@%d:%d — skipping (counted drop)",
                        message.topic(), message.partition(), message.offset())
            consumer.commit(message)
            continue
        # Upsert-then-commit; on connection failure, reconnect and retry the same
        # message. A non-connection error (bad row) is a counted drop, not a
        # poison pill that crash-loops the consumer on the same offset forever.
        while _running:
            try:
                upsert(connection, message.topic(), payload)
                break
            except psycopg.OperationalError as exc:
                log.warning("postgres write failed (%s) — reconnecting", exc)
                try:
                    connection.close()
                except Exception:
                    pass
                connection = connect_pg(os.environ["PG_DSN"])
            except psycopg.Error as exc:
                log.warning("unwritable message on %s@%d:%d (%s) — skipping (counted drop)",
                            message.topic(), message.partition(), message.offset(), exc)
                connection.rollback()
                break
        else:
            break
        consumer.commit(message)
        log.info("upserted %s key=%s", message.topic(),
                 (message.key() or b"").decode(errors="replace"))

    consumer.close()
    connection.close()
    log.info("stopped cleanly")


if __name__ == "__main__":
    main()
