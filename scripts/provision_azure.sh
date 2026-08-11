#!/usr/bin/env bash
# SkyNYC — Azure storage provisioning (PRD §8 v1.3).
#
# Creates the resource group, an ADLS Gen2 storage account (StorageV2 +
# hierarchical namespace), the bronze and backups containers, and a lifecycle
# rule that tiers bronze blobs to Cool at 30 days.
#
# Prereqs: `az login` completed; AZURE_STORAGE_ACCOUNT set in .env (globally
# unique, 3-24 lowercase alphanumerics, e.g. skynycbronze<initials>).
# Idempotent: safe to re-run. Prints no secrets; the key goes straight to .env
# by hand (last step is printed, not executed).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
set -a; source "$REPO_ROOT/.env"; set +a

: "${AZURE_STORAGE_ACCOUNT:?set AZURE_STORAGE_ACCOUNT in .env (3-24 lowercase alphanumerics, globally unique)}"

RG="${AZURE_RESOURCE_GROUP:-skynyc-rg}"
LOCATION="${AZURE_LOCATION:-eastus}"
ACCOUNT="$AZURE_STORAGE_ACCOUNT"

echo "==> resource group $RG ($LOCATION)"
az group create --name "$RG" --location "$LOCATION" --output none

echo "==> storage account $ACCOUNT (StorageV2, LRS, hierarchical namespace, no public access)"
az storage account create \
  --name "$ACCOUNT" --resource-group "$RG" --location "$LOCATION" \
  --sku Standard_LRS --kind StorageV2 --hns true \
  --min-tls-version TLS1_2 --allow-blob-public-access false \
  --output none

echo "==> containers: bronze, backups"
KEY=$(az storage account keys list --account-name "$ACCOUNT" --resource-group "$RG" \
      --query '[0].value' -o tsv)
for c in bronze backups; do
  az storage container create --name "$c" \
    --account-name "$ACCOUNT" --account-key "$KEY" --output none
done

echo "==> lifecycle rule: bronze -> Cool tier at 30 days (PRD §8)"
az storage account management-policy create \
  --account-name "$ACCOUNT" --resource-group "$RG" --policy @- --output none <<'POLICY'
{
  "rules": [
    {
      "enabled": true,
      "name": "bronze-cool-30d",
      "type": "Lifecycle",
      "definition": {
        "actions": { "baseBlob": { "tierToCool": { "daysAfterModificationGreaterThan": 30 } } },
        "filters": { "blobTypes": ["blockBlob"], "prefixMatch": ["bronze/"] }
      }
    }
  ]
}
POLICY

echo
echo "done. Last step, by hand (keeps the key out of shell history logs kept by CI):"
echo "  az storage account keys list --account-name $ACCOUNT --resource-group $RG --query '[0].value' -o tsv"
echo "  -> paste the value into .env as AZURE_STORAGE_KEY (never commit .env)"
