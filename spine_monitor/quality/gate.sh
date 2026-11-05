#!/bin/sh
# Quality gate: every step is blocking. Same command locally and in CI.
set -eu

echo "==> ruff"
ruff check .

echo "==> pyright"
pyright

echo "==> tests + coverage (random order, timeout, real mosquitto)"
pytest -q --cov --cov-report=term-missing --cov-report=xml:coverage.xml

if [ "${GATE_MUTATION:-off}" != "off" ]; then
    echo "==> mutation (${GATE_MUTATION})"
    sh quality/mutation.sh "${GATE_MUTATION}"
fi

echo "==> gate passed"
