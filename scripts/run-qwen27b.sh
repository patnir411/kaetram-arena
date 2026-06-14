#!/usr/bin/env bash
# Launch a base-model run against the self-hosted dense Qwen3.5-27B Modal
# endpoint (finetune/serve_modal_27b.py), using the identical harness env as the
# 9B --qwen-base run: 16K session-budget gate, observe-compaction, and the three
# archetype policies (grinder + completionist + explorer-tinkerer).
#
# The only thing that changes vs a normal --qwen-base run is the served model —
# the endpoint URL is forwarded into orchestrate via KAETRAM_QWEN_BASE_ENDPOINT.
#
# Usage:
#   ./scripts/run-qwen27b.sh            # 3 agents, 5 hours
#   ./scripts/run-qwen27b.sh 8          # 3 agents, 8 hours
#   ./scripts/run-qwen27b.sh 0          # 3 agents, no time limit
#
# Deploy/refresh the endpoint first with:
#   modal deploy finetune/serve_modal_27b.py
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Self-hosted dense Qwen3.5-27B (Modal SGLang, 16K context). Override by
# exporting KAETRAM_QWEN_BASE_ENDPOINT before invoking.
export KAETRAM_QWEN_BASE_ENDPOINT="${KAETRAM_QWEN_BASE_ENDPOINT:-https://patnir411--kaetram-qwen-27b-inference-serve.modal.run/v1}"

# Label the run as 27B base so run.meta.json / dashboard / log_analysis don't
# show it as the 9B "kaetram-base".
export KAETRAM_QWEN_BASE_MODEL="${KAETRAM_QWEN_BASE_MODEL:-kaetram-base-27b}"

HOURS="${1:-5}"

echo "Qwen3.5-27B base run → $KAETRAM_QWEN_BASE_ENDPOINT  (3 agents, ${HOURS}h)"

exec "$SCRIPT_DIR/restart-agent.sh" \
  --qwen-base 3 --grinder 1 --completionist 1 --explorer 1 \
  --hours "$HOURS"
