#!/usr/bin/env bash
# byterover-health.sh -- Check ByteRover BYOK configuration and connectivity
# Usage: ./byterover-health.sh [--json]
# Exit 0 = healthy, 1 = degraded (built-in LLM active), 2 = error

set -euo pipefail

JSON_MODE=0
[[ "${1:-}" == "--json" ]] && JSON_MODE=1

status=0
issues=""

# --- 1. brv binary present ---
if ! command -v brv &>/dev/null; then
  if [[ $JSON_MODE -eq 1 ]]; then
    echo '{"healthy":false,"error":"brv not found in PATH"}'
  else
    echo "FAIL: brv not found in PATH. Install: npm install -g byterover-cli"
  fi
  exit 2
fi

BRV_VERSION=$(brv --version 2>/dev/null | grep -oP 'byterover-cli/\K[0-9.]+' || echo "unknown")

# --- 2. Provider check ---
PROVIDER_JSON=$(brv providers --format json 2>/dev/null || echo '{}')
PROVIDER_ID=$(echo "$PROVIDER_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',{}).get('providerId','unknown'))" 2>/dev/null || echo "unknown")
PROVIDER_NAME=$(echo "$PROVIDER_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',{}).get('providerName','unknown'))" 2>/dev/null || echo "unknown")
MODEL=$(echo "$PROVIDER_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',{}).get('activeModel','unknown'))" 2>/dev/null || echo "unknown")

if [[ "$PROVIDER_ID" == "byterover" ]]; then
  status=1
  issues="${issues}BYOK not configured -- built-in LLM active (credit debt accumulating). Fix: brv providers connect anthropic --api-key \$(op read 'op://Development/Anthropic API/credential')\n"
fi

# --- 3. Connectivity test via quick curate ---
# brv curate --format json streams NDJSON; parse last completed event or use exit code
CURATE_RAW=$(brv curate "byterover-health-check connectivity test $(date +%Y%m%d-%H%M%S)" --format json 2>/dev/null)
CURATE_EXIT=$?
# Look for a completed event with success:true in the NDJSON stream
CURATE_OK=$(echo "$CURATE_RAW" | python3 -c "
import json, sys
for line in sys.stdin:
    try:
        d = json.loads(line)
        if d.get('data', {}).get('event') == 'completed' and d.get('success'):
            print('true')
            sys.exit(0)
    except Exception:
        pass
print('false')
" 2>/dev/null || echo "false")
# Fallback: trust exit code if NDJSON parsing failed
if [[ "$CURATE_OK" != "true" ]] && [[ $CURATE_EXIT -eq 0 ]]; then
  CURATE_OK="true"
fi

if [[ "$CURATE_OK" != "true" ]]; then
  status=2
  issues="${issues}Curate connectivity test failed -- provider may be misconfigured or API key invalid\n"
fi

# --- 4. Space/status ---
SPACE=$(brv status 2>/dev/null | grep "^Space:" | sed 's/Space: //' || echo "unknown")

# --- Output ---
HEALTHY="false"
[[ $status -eq 0 ]] && HEALTHY="true"
CURATE_BOOL="false"
[[ "$CURATE_OK" == "true" ]] && CURATE_BOOL="true"

if [[ $JSON_MODE -eq 1 ]]; then
  ISSUES_JSON=$(printf '%b' "$issues" | python3 -c "
import json, sys
lines = [l for l in sys.stdin.read().splitlines() if l.strip()]
print(json.dumps(lines))
")
  python3 -c "
import json
print(json.dumps({
  'healthy': $HEALTHY,
  'status': $status,
  'version': '$BRV_VERSION',
  'provider': '$PROVIDER_ID',
  'provider_name': '$PROVIDER_NAME',
  'model': '$MODEL',
  'space': '$SPACE',
  'curate_ok': $CURATE_BOOL,
  'issues': $ISSUES_JSON
}, indent=2))
"
else
  echo "=== ByteRover Health Check ==="
  echo "Version  : $BRV_VERSION"
  echo "Provider : $PROVIDER_NAME ($PROVIDER_ID)"
  echo "Model    : $MODEL"
  echo "Space    : $SPACE"
  echo "Curate   : $([[ "$CURATE_OK" == "true" ]] && echo "OK" || echo "FAIL")"
  echo ""
  if [[ $status -eq 0 ]]; then
    echo "STATUS: HEALTHY -- BYOK active, connectivity confirmed"
  elif [[ $status -eq 1 ]]; then
    echo "STATUS: DEGRADED -- built-in LLM active (credit debt accumulating)"
    printf '%b' "$issues" | grep . | while IFS= read -r line; do echo "  WARN: $line"; done
  else
    echo "STATUS: ERROR"
    printf '%b' "$issues" | grep . | while IFS= read -r line; do echo "  FAIL: $line"; done
  fi
fi

exit $status
