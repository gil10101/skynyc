# Unity Catalog plumbing, Azure side. The access connector's managed identity
# is what the serverless SQL warehouse uses to reach the lake — serverless
# compute cannot use the account-key spark conf the jobs use, so UC's storage
# credential is the enabler for the analyst workbench (PRD §18 v1.4.3), not a
# governance ornament.

resource "azurerm_databricks_access_connector" "uc" {
  name                = "skynyc-uc-connector"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_role_assignment" "uc_lake" {
  scope                = azurerm_storage_account.lake.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_databricks_access_connector.uc.identity[0].principal_id
}

output "uc_connector_id" {
  value = azurerm_databricks_access_connector.uc.id
}
