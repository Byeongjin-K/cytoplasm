#!/bin/sh
# Owner-only preparation; no caller in release/install/deploy invokes this block.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$root"
exec python3 -B -m automation.google_gate.root_block "$@"
