#!/usr/bin/env bash
set -euo pipefail

base_url="${1:-http://127.0.0.1:8765}"

health="$(curl --fail --silent --show-error "${base_url}/api/v1/health")"
bootstrap="$(curl --fail --silent --show-error "${base_url}/api/v1/bootstrap")"
products="$(curl --fail --silent --show-error "${base_url}/api/v1/products")"
detail="$(curl --fail --silent --show-error "${base_url}/api/v1/products/darwin-12")"
legacy_health="$(curl --fail --silent --show-error "${base_url}/api/health")"
legacy_config="$(curl --fail --silent --show-error "${base_url}/api/config")"
legacy_state="$(curl --fail --silent --show-error "${base_url}/api/state")"
root_status="$(curl --silent --output /dev/null --write-out '%{http_code}' "${base_url}/")"
missing_status="$(curl --silent --output /dev/null --write-out '%{http_code}' "${base_url}/api/v1/products/missing")"

[[ "${health}" == *'"ok":true'* ]]
[[ "${bootstrap}" == *'超级演示用户'* ]]
[[ "${products}" == *'达尔文12号'* ]]
[[ "${detail}" == *'投保年龄'* ]]
[[ "${legacy_health}" == *'"ok":true'* ]]
[[ "${legacy_config}" == *'deepseekConfigured'* ]]
[[ "${legacy_state}" == *'darwin-12'* ]]
[[ "${root_status}" == "200" ]]
[[ "${missing_status}" == "404" ]]

printf 'Stage 01 smoke checks passed for %s\n' "${base_url}"
