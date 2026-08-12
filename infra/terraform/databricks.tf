# Workspace only. The jobs and workspace files live in the databricks/ submodule
# root (separate state) because the databricks provider needs the workspace URL,
# which does not exist until this layer has applied — the classic two-phase
# provider bootstrap. Apply order: this root first, then databricks/.

resource "azurerm_databricks_workspace" "this" {
  name                        = "skynyc-dbw"
  resource_group_name         = azurerm_resource_group.this.name
  location                    = azurerm_resource_group.this.location
  sku                         = "premium"
  managed_resource_group_name = "skynyc-dbw-managed"
}

output "databricks_workspace_url" {
  value = azurerm_databricks_workspace.this.workspace_url
}
