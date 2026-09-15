#!/usr/bin/env bash
# Grep battery for patterns that are rarely innocent in application source.
#
# Every section prints its hits or "clean". Expect false positives: `eval` in a
# plugin loader and `atob` in an attachment decoder are architecture. What makes
# a FINDING is a credential source wired to a network sink -- read the
# surrounding code before you report anything here.
#
# Test files are skipped by default because they dominate the noise in every
# section; set INCLUDE_TESTS=1 to keep them (worth doing once, since a test
# fixture is a comfortable place to hide a payload).
#
# Usage: bash danger_sweep.sh <repo-root>
set -uo pipefail

ROOT="${1:?usage: danger_sweep.sh <repo-root>}"
cd "$ROOT" || exit 2
: "${INCLUDE_TESTS:=0}"

EXCL='--exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=venv --exclude-dir=__pycache__ --exclude-dir=dist --exclude-dir=build'
SRC='--include=*.ts --include=*.tsx --include=*.js --include=*.jsx --include=*.mjs --include=*.cjs
     --include=*.py --include=*.rb --include=*.go --include=*.rs --include=*.sh --include=*.bash
     --include=*.ps1 --include=*.php --include=*.java --include=*.yml --include=*.yaml'

if [ "$INCLUDE_TESTS" = "1" ]; then
  denoise() { cat; }
else
  denoise() { grep -vE '(^|/)(tests?|__tests__|spec|fixtures?)/|\.(spec|test|e2e)\.' || true; }
fi

TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
section() { printf '\n\033[1m=== %s ===\033[0m\n' "$1"; }
report() {
  local n; n=$(wc -l < "$TMP")
  if [ "$n" -eq 0 ]; then echo "clean"; else
    head -25 "$TMP"; [ "$n" -gt 25 ] && echo "... ($n hits total)"
  fi
}
sweep() { grep -rnIE $EXCL $SRC "$1" . 2>/dev/null | denoise > "$TMP"; report; }

section "Credential / secret material paths"
# \.env is anchored so it does not match process.env / import.meta.env
sweep '\.ssh/|id_rsa|id_ed25519|\.aws/credentials|\.git-credentials|\.docker/config|kube/config|\.config/gcloud|Local Storage/leveldb|Login Data|binarycookies|keychain|keyring|wallet\.dat|metamask|(^|[^A-Za-z0-9_.])\.env(\.|$|["'"'"'`[:space:]])|\.npmrc'

section "Remote code execution (curl|sh and friends)"
grep -rnIE $EXCL 'curl[^|;]*\|[[:space:]]*(ba|z|k)?sh|wget[^|;]*\|[[:space:]]*(ba|z|k)?sh|iwr[^|]*\|[[:space:]]*iex|bash <\(curl|Invoke-Expression' . 2>/dev/null | denoise > "$TMP"; report

section "Dynamic evaluation (imports alone are not findings -- look for string-built commands)"
sweep '\beval\(|new Function\(|exec\(compile\(|pickle\.loads|marshal\.loads|vm\.runInNewContext|execSync\(|exec\([^)]*\+|spawn\([^)]*\+'

section "Encoded payload markers"
sweep 'atob\(|Buffer\.from\([^,)]*,[[:space:]]*.base64|b64decode|fromCharCode\(|(\\x[0-9a-fA-F]{2}){4,}'

section "Exfiltration sinks"
sweep 'sendBeacon|new WebSocket|dgram\.|net\.connect|net\.Socket|urllib\.request|requests\.(post|put)|axios\.(post|put)|fetch\([^)]*method'

section "Outbound hosts (frequency-ranked -- look for the odd one out)"
grep -rhoIE $EXCL $SRC 'https?://[a-zA-Z0-9._~%-]+' . 2>/dev/null \
  | sed -E 's#https?://##' | sort | uniq -c | sort -rn | head -30
echo "(review any host you do not recognize; exfil hides in the long tail)"

section "Minified / obfuscated lines in source (>600 chars)"
find . -type f \( -name '*.ts' -o -name '*.js' -o -name '*.mjs' -o -name '*.py' -o -name '*.rb' \) \
  -not -path './.git/*' -not -path '*/node_modules/*' -print0 2>/dev/null \
  | xargs -0 awk 'length($0)>600 {print FILENAME":"FNR" ("length($0)" chars)"}' 2>/dev/null > "$TMP"; report

section "Checked-in binaries (unreadable -- disclose these in the report)"
find . -type f \( -name '*.node' -o -name '*.so' -o -name '*.dll' -o -name '*.dylib' \
  -o -name '*.exe' -o -name '*.wasm' -o -name '*.jar' -o -name '*.pyc' \) \
  -not -path './.git/*' -not -path '*/node_modules/*' 2>/dev/null > "$TMP"; report

printf '\n\033[1m=== done ===\033[0m\n'
[ "$INCLUDE_TESTS" = "1" ] || echo "(test files excluded; rerun with INCLUDE_TESTS=1 to sweep them too)"
echo "Hits are leads, not findings. Confirm a credential source reaches a network"
echo "sink before calling anything malicious, and trace reachability before"
echo "assigning severity."
