# Remote state lives beside the data it manages: a dedicated tfstate container in the
# project storage account. The container itself is bootstrapped once with az (chicken-egg:
# the backend must exist before init) — everything else in Azure is managed here.
terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }

  backend "azurerm" {
    resource_group_name  = "skynyc-rg"
    storage_account_name = "skynycbronzegil"
    container_name       = "tfstate"
    key                  = "skynyc.tfstate"
    # auth: ARM_ACCESS_KEY exported by scripts/tf.sh from .env — never written here
  }
}

provider "azurerm" {
  features {}
  # subscription via ARM_SUBSCRIPTION_ID; identity via az login (dev machine only)
}
