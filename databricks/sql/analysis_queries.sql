-- Analyst-workbench queries over the historical lakehouse (PRD §13 M4A).
-- These run on the serverless SQL warehouse against the UC external tables;
-- the live product is served from Postgres/Grafana — this layer is for
-- exploration of the 38-year history, not serving.
--
-- Naming: lake.gold_airport_day_delay / lake.silver_ontime map 1:1 to the
-- Delta paths the jobs write; the tables are external, the jobs stay path-based.

-- 1. The thesis, previewed: do low-ceiling/low-visibility days cost arrival
--    performance? 38-year sample, per airport.
SELECT
  airport,
  worst_category,
  count(*)                                   AS days,
  round(avg(avg_arr_delay_min), 1)           AS avg_delay_min,
  round(avg(p90_arr_delay_min), 1)           AS avg_p90_delay_min,
  round(avg(arrivals_delayed_15 / nullif(arrivals_scheduled, 0)) * 100, 1)
                                             AS pct_arrivals_late15
FROM skynyc.lake.gold_airport_day_delay
WHERE worst_category IS NOT NULL
GROUP BY airport, worst_category
ORDER BY airport, array_position(array('VFR','MVFR','IFR','LIFR'), worst_category);

-- 2. Worst weather days on record per airport (Sandy 2012-10-29 and the
--    2001-09 ground stop both surface here — recognizable ground truth).
SELECT
  flight_date, airport, worst_category,
  arrivals_scheduled, cancelled, cancelled_weather,
  round(weather_delay_min_total / 60, 1) AS weather_delay_hours
FROM skynyc.lake.gold_airport_day_delay
QUALIFY row_number() OVER (PARTITION BY airport ORDER BY cancelled_weather DESC) <= 10
ORDER BY cancelled_weather DESC;

-- 3. Weather's share of attributed delay by year (cause attribution exists
--    from June 2003; earlier years are NULL by source design, never zero).
SELECT
  year(flight_date) AS yr,
  round(sum(weather_delay_min_total) / 60000, 1)                    AS weather_khrs,
  round(sum(nas_delay_min_total) / 60000, 1)                        AS nas_khrs,
  round(sum(weather_delay_min_total)
        / nullif(sum(weather_delay_min_total) + sum(nas_delay_min_total), 0) * 100, 1)
                                                                    AS weather_pct_of_wx_nas
FROM skynyc.lake.gold_airport_day_delay
WHERE flight_date >= '2003-06-01'
GROUP BY 1 ORDER BY 1;

-- 4. Seasonality: weather cancellations by month x airport, full history.
SELECT
  month(flight_date)                AS mon,
  airport,
  sum(cancelled_weather)            AS wx_cancels,
  round(avg(pct_obs_ifr_or_worse) * 100, 1) AS pct_obs_ifr_plus
FROM skynyc.lake.gold_airport_day_delay
GROUP BY 1, 2 ORDER BY 1, 2;

-- 5. Volume receipt: what the lakehouse actually holds, by layer grain.
SELECT 'silver_ontime' AS tbl, count(*) AS rows, min(flight_date) AS from_date,
       max(flight_date) AS to_date, count(DISTINCT dest) AS airports
FROM skynyc.lake.silver_ontime
UNION ALL
SELECT 'gold_airport_day_delay', count(*), min(flight_date), max(flight_date),
       count(DISTINCT airport)
FROM skynyc.lake.gold_airport_day_delay;

-- 6. The 2001-09 ground stop, day by day (national scheduled arrivals into
--    the NYC three; the week the system stopped).
SELECT flight_date,
       sum(arrivals_scheduled)          AS scheduled,
       sum(cancelled)                   AS cancelled,
       round(sum(cancelled) / nullif(sum(arrivals_scheduled), 0) * 100, 1)
                                        AS pct_cancelled
FROM skynyc.lake.gold_airport_day_delay
WHERE flight_date BETWEEN '2001-09-08' AND '2001-09-20'
GROUP BY 1 ORDER BY 1;
