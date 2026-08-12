# Unity Catalog objects + the serverless analyst warehouse (PRD §18 v1.4.3).
# The external tables are read-only windows onto the Delta paths the jobs
# already write — jobs stay path-based, serving stays in Postgres; this layer
# exists so the serverless warehouse can query the lake at all.

data "terraform_remote_state" "azure" {
  backend = "azurerm"
  config = {
    resource_group_name  = "skynyc-rg"
    storage_account_name = "skynycbronzegil"
    container_name       = "tfstate"
    key                  = "skynyc.tfstate"
  }
}

resource "databricks_storage_credential" "lake" {
  name = "skynyc-lake-cred"
  azure_managed_identity {
    access_connector_id = data.terraform_remote_state.azure.outputs.uc_connector_id
  }
  comment = "Access connector managed identity - serverless warehouse path to the lake"
}

resource "databricks_external_location" "lake" {
  name            = "skynyc-lake"
  url             = "abfss://lake@${var.storage_account_name}.dfs.core.windows.net/"
  credential_name = databricks_storage_credential.lake.name
  comment         = "Delta medallion written by the jobs; read here by the warehouse"
}

# The auto-provisioned regional metastore has no root storage, so the catalog
# carries its own storage_root inside the governed location.
resource "databricks_catalog" "skynyc" {
  name         = "skynyc"
  storage_root = "abfss://lake@${var.storage_account_name}.dfs.core.windows.net/uc"
  comment      = "Analyst-facing views of the historical lakehouse"
  depends_on   = [databricks_external_location.lake]
}

resource "databricks_schema" "lake" {
  catalog_name = databricks_catalog.skynyc.name
  name         = "lake"
}

resource "databricks_sql_endpoint" "analyst" {
  name                      = "skynyc-analyst"
  cluster_size              = "2X-Small"
  max_num_clusters          = 1
  auto_stop_mins            = 5
  enable_serverless_compute = true
  warehouse_type            = "PRO"
}

locals {
  external_tables = {
    gold_airport_day_delay = "gold/airport_day_delay"
    silver_ontime          = "silver/ontime"
    silver_metar           = "silver/metar"
  }
}

resource "databricks_sql_table" "lake" {
  for_each           = local.external_tables
  name               = each.key
  catalog_name       = databricks_catalog.skynyc.name
  schema_name        = databricks_schema.lake.name
  table_type         = "EXTERNAL"
  data_source_format = "DELTA"
  storage_location   = "abfss://lake@${var.storage_account_name}.dfs.core.windows.net/${each.value}"
  warehouse_id       = databricks_sql_endpoint.analyst.id
}

output "warehouse_id" {
  value = databricks_sql_endpoint.analyst.id
}
