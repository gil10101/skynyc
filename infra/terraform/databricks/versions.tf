# Second root module: everything INSIDE the Databricks workspace. Separate state
# because the databricks provider cannot be configured until the workspace (created
# by the parent root) exists. Apply order: .. first, then this directory.
terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.50"
    }
  }

  backend "azurerm" {
    resource_group_name  = "skynyc-rg"
    storage_account_name = "skynycbronzegil"
    container_name       = "tfstate"
    key                  = "skynyc-databricks.tfstate"
  }
}

provider "azurerm" {
  features {}
}

data "azurerm_databricks_workspace" "this" {
  name                = "skynyc-dbw"
  resource_group_name = var.resource_group_name
}

provider "databricks" {
  host      = "https://${data.azurerm_databricks_workspace.this.workspace_url}"
  auth_type = "azure-cli"
}
