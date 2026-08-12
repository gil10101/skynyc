variable "location" {
  type    = string
  default = "eastus"
}

variable "storage_account_name" {
  type    = string
  default = "skynycbronzegil"
}

variable "resource_group_name" {
  type    = string
  default = "skynyc-rg"
}

# Storage account key, injected as TF_VAR_storage_key by scripts/tf.sh from .env.
# Used by ADF's ADLS linked service and the Databricks job clusters' spark conf.
# It lands in remote state (private container) — rotate via the portal if that
# container's access is ever widened.
variable "storage_key" {
  type      = string
  sensitive = true
}

# Budget alert recipient, injected as TF_VAR_alert_email from .env CONTACT_EMAIL.
variable "alert_email" {
  type      = string
  sensitive = true
}
