#!/usr/bin/env bash
# Firewall audit: agent-side code must never mention grading-side fields.
#
# The benchmark's integrity rests on the agent not seeing gold_patch,
# test_patch, or the graded test lists. Types make the leak unlikely;
# this grep makes it impossible to miss in review. Wire into CI later;
# run by hand until then. Exits 1 on any hit.
set -euo pipefail
cd "$(dirname "$0")/.."

# Agent-side packages, guarded before they exist so nobody has to remember
# to extend this list on day 8 when retrieval/ and agent/ land.
targets=()
for d in fixpoint/agent fixpoint/retrieval; do
  [ -d "$d" ] && targets+=("$d")
done

if [ ${#targets[@]} -eq 0 ]; then
  echo "leak-audit: no agent-side packages yet — trivially clean"
  exit 0
fi

pattern='gold_patch|test_patch|fail_to_pass|pass_to_pass|FAIL_TO_PASS|PASS_TO_PASS|hints_text'
# --include='*.py': scan source only, never compiled .pyc (which embed docstrings).
if grep -rnE --include='*.py' "$pattern" "${targets[@]}"; then
  echo "leak-audit: FAIL — grading-side fields referenced in agent-side code (hits above)"
  exit 1
fi
echo "leak-audit: clean"
