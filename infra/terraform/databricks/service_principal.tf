# The orchestrator gets its own identity. Dagster triggers runs with this
# service principal's token instead of a user PAT, so the run page credits
# dagster-orchestrator — the caller identity is the receipt for the
# "Dagster is the control plane" claim.

resource "databricks_service_principal" "dagster" {
  display_name = "dagster-orchestrator"
}

# run-now against the standing job needs no compute entitlement (the job owns
# its cluster spec), but one-off runs/submit validation runs do.
resource "databricks_entitlements" "dagster" {
  service_principal_id = databricks_service_principal.dagster.id
  workspace_access     = true
  allow_cluster_create = true
}

resource "databricks_permissions" "medallion_run" {
  job_id = databricks_job.medallion.id
  access_control {
    service_principal_name = databricks_service_principal.dagster.application_id
    permission_level       = "CAN_MANAGE_RUN"
  }
}

# OAuth M2M credential: Dagster exchanges client id + secret for a one-hour
# token per run (client_credentials against /oidc/v1/token). No long-lived
# bearer token exists anywhere — strictly better than the OBO/PAT path this
# replaced (which the workspace tier disables anyway).
resource "databricks_service_principal_secret" "dagster" {
  service_principal_id = databricks_service_principal.dagster.id
}

output "dagster_client_id" {
  value = databricks_service_principal.dagster.application_id
}

output "dagster_client_secret" {
  value     = databricks_service_principal_secret.dagster.secret
  sensitive = true
}
