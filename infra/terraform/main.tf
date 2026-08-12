# Core Azure resources. The resource group, storage account, its two original
# containers, and the lifecycle policy predate Terraform (created by the v1.3
# provisioning script) — they are IMPORTED, never recreated. Arguments below match
# the live config exactly; a plan that wants to touch the storage account is a
# drift signal, not something to apply blindly.

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_storage_account" "lake" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.this.name
  location                 = azurerm_resource_group.this.location
  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "LRS"
  access_tier              = "Hot"
  is_hns_enabled           = true
  min_tls_version          = "TLS1_2"
  allow_nested_items_to_be_public = false
  https_traffic_only_enabled      = true
}

# Pre-existing containers (v1.3): streaming bronze + pg_dump backups.
resource "azurerm_storage_container" "bronze" {
  name                  = "bronze"
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "backups" {
  name                  = "backups"
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

# New for the historical layer (v1.4): immutable landed files and the Delta medallion.
resource "azurerm_storage_container" "raw" {
  name                  = "raw"
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "lakehouse" {
  name                  = "lake"
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

resource "azurerm_storage_management_policy" "cool" {
  storage_account_id = azurerm_storage_account.lake.id

  rule {
    name    = "bronze-cool-30d"
    enabled = true
    filters {
      prefix_match = ["bronze/states"]
      blob_types   = ["blockBlob"]
    }
    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than = 30
      }
    }
  }
}

# Guardrail from PRD §12: alert while burning the credit grant, not after.
resource "azurerm_consumption_budget_resource_group" "monthly" {
  name              = "skynyc-monthly-25"
  resource_group_id = azurerm_resource_group.this.id
  amount            = 25
  time_grain        = "Monthly"

  time_period {
    start_date = "2026-08-01T00:00:00Z"
  }

  notification {
    enabled        = true
    threshold      = 80
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = [var.alert_email]
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = [var.alert_email]
  }
}
