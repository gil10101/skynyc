# Jobs compute only (PRD §3 v1.4): a single-node job cluster that exists for the
# duration of a run. No all-purpose clusters, nothing serves from here — this
# workspace exists to chew ~230M rows of BTS history into the Delta medallion.

variable "resource_group_name" {
  type    = string
  default = "skynyc-rg"
}

variable "storage_account_name" {
  type    = string
  default = "skynycbronzegil"
}

variable "storage_key" {
  type      = string
  sensitive = true
}

locals {
  job_files = ["bronze_bts.py", "silver_ontime.py", "silver_metar.py", "gold_marts.py"]

  raw_base  = "abfss://raw@${var.storage_account_name}.dfs.core.windows.net"
  lake_base = "abfss://lake@${var.storage_account_name}.dfs.core.windows.net"

  spark_conf = {
    "spark.databricks.cluster.profile" = "singleNode"
    "spark.master"                     = "local[*]"
    # Direct account-key auth to ADLS — same pattern the streaming app uses.
    "fs.azure.account.key.${var.storage_account_name}.dfs.core.windows.net" = var.storage_key
  }
}

resource "databricks_workspace_file" "jobs" {
  for_each = toset(local.job_files)
  source   = "${path.module}/../../../databricks/jobs/${each.value}"
  path     = "/Shared/skynyc/jobs/${each.value}"
}

resource "databricks_job" "medallion" {
  name                = "skynyc-medallion-build"
  max_concurrent_runs = 1

  job_cluster {
    job_cluster_key = "single_node"
    new_cluster {
      spark_version = "15.4.x-scala2.12"
      node_type_id  = "Standard_DS3_v2"
      num_workers   = 0
      spark_conf    = local.spark_conf
      custom_tags   = { "ResourceClass" = "SingleNode" }
    }
  }

  task {
    task_key        = "bronze_bts"
    job_cluster_key = "single_node"
    spark_python_task {
      python_file = databricks_workspace_file.jobs["bronze_bts.py"].path
      source      = "WORKSPACE"
      parameters  = [local.raw_base, local.lake_base]
    }
    timeout_seconds = 21600
  }

  # Task blocks are compared positionally against the API, which returns them
  # sorted by task_key — keep these alphabetical or every plan shows a phantom
  # in-place update.
  task {
    task_key        = "gold_marts"
    job_cluster_key = "single_node"
    depends_on { task_key = "silver_metar" }
    depends_on { task_key = "silver_ontime" }
    spark_python_task {
      python_file = databricks_workspace_file.jobs["gold_marts.py"].path
      source      = "WORKSPACE"
      parameters  = [local.lake_base]
    }
    timeout_seconds = 7200
  }

  task {
    task_key        = "silver_metar"
    job_cluster_key = "single_node"
    depends_on { task_key = "bronze_bts" }
    spark_python_task {
      python_file = databricks_workspace_file.jobs["silver_metar.py"].path
      source      = "WORKSPACE"
      parameters  = [local.raw_base, local.lake_base]
    }
    timeout_seconds = 7200
  }

  task {
    task_key        = "silver_ontime"
    job_cluster_key = "single_node"
    depends_on { task_key = "bronze_bts" }
    spark_python_task {
      python_file = databricks_workspace_file.jobs["silver_ontime.py"].path
      source      = "WORKSPACE"
      parameters  = [local.lake_base]
    }
    timeout_seconds = 14400
  }
}

output "medallion_job_id" {
  value = databricks_job.medallion.id
}
