#!/usr/bin/env bash
# Run all test files individually to avoid cross-file event loop contamination.
# pytest-asyncio 1.3.0 + strict mode leaks event loops between files,
# causing hangs when async and sync test files are collected together.
#
# Usage: ./run_tests.sh [pytest-args...]
# Example: ./run_tests.sh -x --timeout=10

set -uo pipefail

TESTS_DIR="$(dirname "$0")/tests"
TOTAL=0
PASSED=0
FAILED=0
FAILED_FILES=()

for f in "$TESTS_DIR"/test_*.py; do
    fname="$(basename "$f")"
    printf "%-40s " "$fname"

    output=$(python -m pytest "$f" -q --timeout=30 "$@" 2>&1)
    rc=$?

    # Extract summary line (e.g. "16 passed in 3.47s")
    summary=$(echo "$output" | grep -E "passed|failed|error" | tail -1)

    if [ $rc -eq 0 ]; then
        echo "✅ $summary"
        ((PASSED++))
    else
        echo "❌ $summary"
        FAILED_FILES+=("$fname")
        ((FAILED++))
    fi
    ((TOTAL++))
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Total: $TOTAL files | ✅ $PASSED passed | ❌ $FAILED failed"
if [ ${#FAILED_FILES[@]} -gt 0 ]; then
    echo "Failed: ${FAILED_FILES[*]}"
    exit 1
fi
