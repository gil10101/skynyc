# Saved analyst queries, managed like everything else. Text lives in
# databricks/sql/analysis_queries.sql (the documentation copy); these are the
# deployed instances bound to the serverless warehouse.

resource "databricks_directory" "queries" {
  path = "/Shared/skynyc/queries"
}

locals {
  queries = {
    "01 thesis - delay by flight category" = <<-SQL
      SELECT airport, worst_category, count(*) AS days,
             round(avg(avg_arr_delay_min), 1)  AS avg_delay_min,
             round(avg(p90_arr_delay_min), 1)  AS avg_p90_delay_min,
             round(avg(arrivals_delayed_15 / nullif(arrivals_scheduled, 0)) * 100, 1) AS pct_arrivals_late15
      FROM skynyc.lake.gold_airport_day_delay
      WHERE worst_category IS NOT NULL
      GROUP BY airport, worst_category
      ORDER BY airport, array_position(array('VFR','MVFR','IFR','LIFR'), worst_category)
    SQL
    "02 worst weather days on record" = <<-SQL
      SELECT flight_date, airport, worst_category,
             arrivals_scheduled, cancelled, cancelled_weather,
             round(weather_delay_min_total / 60, 1) AS weather_delay_hours
      FROM skynyc.lake.gold_airport_day_delay
      QUALIFY row_number() OVER (PARTITION BY airport ORDER BY cancelled_weather DESC) <= 10
      ORDER BY cancelled_weather DESC
    SQL
    "03 weather share of delay by year" = <<-SQL
      SELECT year(flight_date) AS yr,
             round(sum(weather_delay_min_total) / 60000, 1) AS weather_khrs,
             round(sum(nas_delay_min_total) / 60000, 1)     AS nas_khrs,
             round(sum(weather_delay_min_total)
                   / nullif(sum(weather_delay_min_total) + sum(nas_delay_min_total), 0) * 100, 1)
               AS weather_pct_of_wx_nas
      FROM skynyc.lake.gold_airport_day_delay
      WHERE flight_date >= '2003-06-01'
      GROUP BY 1 ORDER BY 1
    SQL
    "04 seasonality - wx cancels by month" = <<-SQL
      SELECT month(flight_date) AS mon, airport,
             sum(cancelled_weather) AS wx_cancels,
             round(avg(pct_obs_ifr_or_worse) * 100, 1) AS pct_obs_ifr_plus
      FROM skynyc.lake.gold_airport_day_delay
      GROUP BY 1, 2 ORDER BY 1, 2
    SQL
    "05 lakehouse volume receipt" = <<-SQL
      SELECT 'silver_ontime' AS tbl, count(*) AS rows, min(flight_date) AS from_date,
             max(flight_date) AS to_date, count(DISTINCT dest) AS airports
      FROM skynyc.lake.silver_ontime
      UNION ALL
      SELECT 'gold_airport_day_delay', count(*), min(flight_date), max(flight_date),
             count(DISTINCT airport)
      FROM skynyc.lake.gold_airport_day_delay
    SQL
    "06 the 2001-09 ground stop" = <<-SQL
      SELECT flight_date,
             sum(arrivals_scheduled) AS scheduled,
             sum(cancelled)          AS cancelled,
             round(sum(cancelled) / nullif(sum(arrivals_scheduled), 0) * 100, 1) AS pct_cancelled
      FROM skynyc.lake.gold_airport_day_delay
      WHERE flight_date BETWEEN '2001-09-08' AND '2001-09-20'
      GROUP BY 1 ORDER BY 1
    SQL
  }
}

resource "databricks_query" "analysis" {
  for_each     = local.queries
  warehouse_id = databricks_sql_endpoint.analyst.id
  display_name = each.key
  query_text   = each.value
  parent_path  = databricks_directory.queries.path
}
