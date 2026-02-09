#!/usr/bin/env bash
# Run all Money system tests (both modules sequentially).
# Each module uses its own venv and sys.path.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FAIL=0

echo "=== moves tests ==="
cd "$SCRIPT_DIR/moves"
if .venv/bin/python -m pytest tests/ -q --timeout=30 "$@"; then
    echo "✅ moves: PASSED"
else
    echo "❌ moves: FAILED"
    FAIL=1
fi

echo ""
echo "=== thoughts tests ==="
cd "$SCRIPT_DIR/thoughts"
if .venv/bin/python -m pytest tests/ -q "$@"; then
    echo "✅ thoughts: PASSED"
else
    echo "❌ thoughts: FAILED"
    FAIL=1
fi

echo ""
if [ $FAIL -eq 0 ]; then
    echo "🎉 All tests passed!"
else
    echo "💥 Some tests failed."
    exit 1
fi
