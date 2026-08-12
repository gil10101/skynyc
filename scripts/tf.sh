#!/usr/bin/env bash
# Terraform wrapper: maps .env secrets to the environment Terraform expects,
# then runs whatever terraform command was passed. Keeps credentials out of
# shell history and out of the .tf files.
#
#   scripts/tf.sh plan
#   scripts/tf.sh apply
set -euo pipefail
cd "$(dirname "$0")/../infra/terraform"

set -a; source ../../.env; set +a

export ARM_SUBSCRIPTION_ID="${ARM_SUBSCRIPTION_ID:?set in .env}"
export ARM_ACCESS_KEY="${AZURE_STORAGE_KEY:?set in .env}"      # backend state auth
export TF_VAR_storage_key="${AZURE_STORAGE_KEY}"
export TF_VAR_alert_email="${CONTACT_EMAIL:?set in .env}"

exec terraform "$@"
