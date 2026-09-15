#!/usr/bin/env bash
# Classify every source file in a fork against its upstream, with rebrand noise
# normalized away.
#
# A rebranded fork shows thousands of diffs that are pure find-and-replace. Pass
# the project's old and new names as TOKENS and they are folded to a common
# string before comparison, so what is left is real. Without this the signal is
# unfindable.
#
# Output per file:
#   NEW   fork-authored, no upstream counterpart -- read ALL of these
#   DIFF  changed from upstream -- read the security-relevant ones
#   GONE  upstream file the fork dropped (often staleness, sometimes removal of a guard)
#
# Prepare worktrees first (cheap, and keeps git out of the comparison):
#   git -C fork     archive HEAD          | (mkdir -p FW && tar -x -C FW)
#   git -C upstream archive <base-commit> | (mkdir -p UW && tar -x -C UW)
#
# Usage: bash fork_diff.sh FW UW [token ...]
#   e.g. bash fork_diff.sh FW UW deepseek-harness pentest-harness @deepseek-ai @pentest-harness
set -uo pipefail

FORK="${1:?usage: fork_diff.sh <fork-tree> <upstream-tree> [rebrand-token ...]}"
UP="${2:?usage: fork_diff.sh <fork-tree> <upstream-tree> [rebrand-token ...]}"
shift 2
FORK=$(cd "$FORK" && pwd) || exit 2
UP=$(cd "$UP" && pwd) || exit 2

# Build one sed program folding every supplied token to a constant.
# Longest first: if "@acme-corp" is folded after "acme-corp", the "@" survives on
# one side only and every file containing the scope reports a false difference.
SEDPROG=""
mapfile -t _TOKENS < <(for t in "$@"; do printf '%s\t%s\n' "${#t}" "$t"; done | sort -rn | cut -f2-)
for tok in "${_TOKENS[@]}"; do
  esc=$(printf '%s' "$tok" | sed -e 's/[]\/$*.^[]/\\&/g')
  SEDPROG="${SEDPROG}s/${esc}/__NAME__/Ig; "
done
[ -z "$SEDPROG" ] && SEDPROG='s/\x00//;'   # no-op when no tokens given

norm() { sed -E "$SEDPROG" "$1" 2>/dev/null; }

# Built as an array so the patterns reach find intact instead of being
# glob-expanded against the current directory.
EXTS=()
for e in ts tsx js jsx mjs cjs py rb go rs sh ps1 java php yml yaml toml; do
  EXTS+=(-o -name "*.$e")
done
EXTS=("${EXTS[@]:1}")   # drop the leading -o

new=0; diff_n=0; same=0; gone=0
NEWF=$(mktemp); DIFFF=$(mktemp); GONEF=$(mktemp)
trap 'rm -f "$NEWF" "$DIFFF" "$GONEF"' EXIT

cd "$FORK" || exit 2
while IFS= read -r f; do
  if [ ! -f "$UP/$f" ]; then
    echo "$f" >> "$NEWF"; new=$((new+1))
  elif diff -q <(norm "$f") <(norm "$UP/$f") >/dev/null 2>&1; then
    same=$((same+1))
  else
    echo "$f" >> "$DIFFF"; diff_n=$((diff_n+1))
  fi
done < <(find . \( "${EXTS[@]}" \) -not -path './.git/*' -not -path '*/node_modules/*' | sort)

cd "$UP" || exit 2
while IFS= read -r f; do
  [ -f "$FORK/$f" ] || { echo "$f" >> "$GONEF"; gone=$((gone+1)); }
done < <(find . \( "${EXTS[@]}" \) -not -path './.git/*' -not -path '*/node_modules/*' | sort)

istest() { grep -E '(^|/)(tests?|__tests__|spec|fixtures?)/|\.(spec|test|e2e)\.'; }
notest() { grep -vE '(^|/)(tests?|__tests__|spec|fixtures?)/|\.(spec|test|e2e)\.' || true; }

printf '\n\033[1m=== NEW (fork-authored -- read all of these) ===\033[0m\n'
notest < "$NEWF" | sed 's/^/  /' || true
n_t=$(istest < "$NEWF" | wc -l); [ "$n_t" -gt 0 ] && echo "  (+ $n_t test files)"

printf '\n\033[1m=== DIFF (changed from upstream) ===\033[0m\n'
notest < "$DIFFF" | sed 's/^/  /' || true
d_t=$(istest < "$DIFFF" | wc -l); [ "$d_t" -gt 0 ] && echo "  (+ $d_t test files)"

printf '\n\033[1m=== GONE (dropped from upstream) ===\033[0m\n'
notest < "$GONEF" | head -40 | sed 's/^/  /' || true
g_all=$(wc -l < "$GONEF"); [ "$g_all" -gt 40 ] && echo "  ... ($g_all total -- a large count usually means the fork is behind upstream)"

printf '\n\033[1m=== SUMMARY ===\033[0m\n'
printf '  identical to upstream: %s\n  changed:               %s\n  fork-authored:         %s\n  dropped:               %s\n' \
  "$same" "$diff_n" "$new" "$gone"
cat <<'NOTE'

Next: read every NEW non-test file, then the DIFF files under security-relevant
paths (auth, crypto, sandbox, network, exec, build, credential handling).
For a stronger claim than file-by-file reading, compare SUBTREE HASHES --
`git rev-parse <commit>:<subdir>` matching means that subtree is byte-identical.
NOTE
