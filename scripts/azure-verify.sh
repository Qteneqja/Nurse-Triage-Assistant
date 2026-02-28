#!/usr/bin/env bash
# =============================================================================
# Post-Deployment Verification — Nurse Triage Assistant
#
# 2-minute checklist to confirm the Azure deployment is healthy.
#
# Usage:
#   ./scripts/azure-verify.sh
#   ./scripts/azure-verify.sh https://your-custom-domain.com
# =============================================================================

set -euo pipefail

# Load deployment info if available
if [[ -f .azure-deploy-info ]]; then
    # shellcheck source=/dev/null
    source .azure-deploy-info
fi

# Allow passing FQDN as argument or derive from .azure-deploy-info
if [[ -n "${1:-}" ]]; then
    BASE_URL="$1"
elif [[ -n "${APP_FQDN:-}" ]]; then
    BASE_URL="https://${APP_FQDN}"
else
    # Try to fetch from Azure
    RG="${RG:-nurse-triage-rg}"
    APP="${APP:-nurse-triage-api}"
    APP_FQDN=$(az containerapp show \
        --resource-group "$RG" \
        --name "$APP" \
        --query properties.configuration.ingress.fqdn -o tsv 2>/dev/null || echo "")
    if [[ -z "$APP_FQDN" ]]; then
        echo "ERROR: Cannot determine app URL."
        echo "  Usage: $0 https://<your-fqdn>"
        exit 1
    fi
    BASE_URL="https://${APP_FQDN}"
fi

echo "============================================================"
echo " Nurse Triage Assistant — Deployment Verification"
echo "============================================================"
echo " Target: $BASE_URL"
echo ""

PASS=0
FAIL=0

# Helper
check() {
    local label="$1"
    local url="$2"
    local expected_status="${3:-200}"

    printf "  %-30s" "$label"

    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$url" 2>/dev/null || echo "000")

    if [[ "$HTTP_STATUS" == "$expected_status" ]]; then
        echo "PASS  (HTTP $HTTP_STATUS)"
        PASS=$((PASS + 1))
    else
        echo "FAIL  (HTTP $HTTP_STATUS, expected $expected_status)"
        FAIL=$((FAIL + 1))
    fi
}

check_json() {
    local label="$1"
    local url="$2"
    local json_key="$3"
    local expected_value="$4"

    printf "  %-30s" "$label"

    RESPONSE=$(curl -s --max-time 10 "$url" 2>/dev/null || echo "{}")
    ACTUAL=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('$json_key',''))" 2>/dev/null || echo "")

    if [[ "$ACTUAL" == "$expected_value" ]]; then
        echo "PASS  ($json_key=$ACTUAL)"
        PASS=$((PASS + 1))
    else
        echo "FAIL  ($json_key='$ACTUAL', expected '$expected_value')"
        FAIL=$((FAIL + 1))
    fi
}

# ---- Checks ----

echo "1. Endpoint Checks"
echo "   -----------------------------------------------"
check "Root endpoint"          "$BASE_URL/"
check "Health (liveness)"      "$BASE_URL/health"
check "Readiness (DB)"         "$BASE_URL/ready"
check "Metrics endpoint"       "$BASE_URL/metrics"
echo ""

echo "2. Response Validation"
echo "   -----------------------------------------------"
check_json "Health status"     "$BASE_URL/health"  "status"  "healthy"
check_json "Ready status"      "$BASE_URL/ready"   "status"  "ready"
check_json "API version"       "$BASE_URL/"        "version" "5.0.0"
echo ""

echo "3. Security Headers"
echo "   -----------------------------------------------"
printf "  %-30s" "X-Request-ID present"
HEADERS=$(curl -s -D - -o /dev/null --max-time 10 "$BASE_URL/health" 2>/dev/null || echo "")
if echo "$HEADERS" | grep -qi "x-request-id"; then
    echo "PASS"
    PASS=$((PASS + 1))
else
    echo "FAIL  (missing X-Request-ID header)"
    FAIL=$((FAIL + 1))
fi
echo ""

echo "4. HTTPS/TLS"
echo "   -----------------------------------------------"
printf "  %-30s" "TLS certificate valid"
if curl -s --max-time 10 "$BASE_URL/health" > /dev/null 2>&1; then
    echo "PASS"
    PASS=$((PASS + 1))
else
    echo "FAIL  (TLS error or unreachable)"
    FAIL=$((FAIL + 1))
fi
echo ""

echo "5. Twilio Webhook (informational)"
echo "   -----------------------------------------------"
VOICE_URL="$BASE_URL/api/v1/voice/incoming"
printf "  %-30s" "Voice webhook reachable"
VOICE_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X POST "$VOICE_URL" 2>/dev/null || echo "000")
# Expect 400 or 422 (missing params) rather than 404/500 — confirms route exists
if [[ "$VOICE_STATUS" =~ ^(400|403|422)$ ]]; then
    echo "PASS  (HTTP $VOICE_STATUS — route exists, params required)"
    PASS=$((PASS + 1))
elif [[ "$VOICE_STATUS" == "200" ]]; then
    echo "PASS  (HTTP 200)"
    PASS=$((PASS + 1))
else
    echo "WARN  (HTTP $VOICE_STATUS — may need Twilio signature)"
    # Don't count as failure since sig validation may block bare requests
fi
echo ""

# ---- Summary ----

TOTAL=$((PASS + FAIL))
echo "============================================================"
echo " Results: $PASS/$TOTAL passed"
if [[ $FAIL -eq 0 ]]; then
    echo " Status: ALL CHECKS PASSED"
else
    echo " Status: $FAIL CHECK(S) FAILED"
fi
echo "============================================================"
echo ""
echo " Twilio Webhook URL (set in Twilio Console):"
echo "   POST $BASE_URL/api/v1/voice/incoming"
echo ""

exit $FAIL
