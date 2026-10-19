#!/usr/bin/env bash
# Hermes interop is retired. Release-approved absence convergence owns cleanup.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if (($#)); then
  echo "usage: automation/interop/deploy.sh (Hermes interop retired; no restart option)" >&2
  exit 2
fi
exec bash "$repo_root/automation/retired_jobs/deploy.sh"
