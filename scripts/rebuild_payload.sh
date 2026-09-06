#!/usr/bin/env bash
# Rebuild the Trix ToolRush payload from a hermes-agent source repo.
#
# Usage: scripts/rebuild_payload.sh [HERMES_SRC] [BASELINE_REF]
#   HERMES_SRC   live/origin hermes-agent checkout (read-only; defaults to
#                ~/.hermes/hermes-agent)
#   BASELINE_REF unpatched upstream git ref inside that repo (default HEAD)
#
# Pipeline: local --shared staging clone (the source repo is never written)
# -> stage_rows applies upstream ToolRush lane rows with Trix renames
# -> build_payload diffs the staged tree against the baseline.
# Run this with the SAME python minor as the target runtime (payload stamp).
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_SRC="${1:-$HOME/.hermes/hermes-agent}"
BASELINE="${2:-HEAD}"
STAGE="${TRIX_STAGE_DIR:-/tmp/trix-toolrush-stage}"
UPSTREAM_PAYLOAD="${TRIX_UPSTREAM_PAYLOAD:-/tmp/opencode/toolrush/v2/plugin/payload.json}"
PY="${TRIX_BUILD_PYTHON:-$HERMES_SRC/venv/bin/python}"

[ -f "$UPSTREAM_PAYLOAD" ] || { echo "upstream ToolRush payload not found: $UPSTREAM_PAYLOAD" >&2; exit 1; }

if [ -d "$STAGE" ]; then
    git -C "$STAGE" checkout -- . >/dev/null
else
    git clone --shared -q "$HERMES_SRC" "$STAGE"
fi
echo "staging tree at $STAGE ($(git -C "$STAGE" rev-parse --short HEAD))"

"$PY" "$HERE/scripts/stage_rows.py" --payload "$UPSTREAM_PAYLOAD" --lanes snapshot --root "$STAGE" \
    --rename 'if self._snapshot_exclusion_broken=if getattr(self, "_snapshot_exclusion_broken", False)' \
    --rename 'not self._snapshot_exclusion_broken=not getattr(self, "_snapshot_exclusion_broken", False)'

"$PY" "$HERE/scripts/stage_rows.py" --payload "$UPSTREAM_PAYLOAD" --lanes rpc --root "$STAGE" \
    --rename '__toolrush_parallel__=__trix_rush_parallel__' \
    --rename 'tools.toolrush_rpc=tools.trix_rush_rpc' \
    --rename 'ToolRush=Trix ToolRush'

cd "$HERE"
"$PY" build_payload.py --root "$STAGE" --baseline "$BASELINE"
