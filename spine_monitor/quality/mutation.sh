#!/bin/sh
# Mutation testing with mutmut, one workspace package at a time.
#
#   sh quality/mutation.sh changed [file ...]   score of the */src/**/*.py files changed vs the base;
#                                               exit 1 when below MUTATION_THRESHOLD (80 %), 0 when nothing changed.
#   sh quality/mutation.sh full                 score per package; always exits 0.
#
# Run from spine_monitor/. Explicit files (paths relative to spine_monitor/) replace the git diff in `changed`.
# Base for `changed`: $MUTATION_BASE, else the first of origin/develop, develop, origin/main, main that exists.
# Only committed changes count (git diff base...HEAD): on a Windows checkout the container's git would see
# every CRLF file in the working tree as modified.
#
# Why a staged copy: mutmut names mutants after the file path minus a leading "src/", and only puts
# mutants/, mutants/src and mutants/source on sys.path. So it must run with the package dir as cwd and
# source_paths = ["src"]. The tests locate fixtures with Path(__file__).parents[N] (../configs, ../../contracts),
# so mutants/ mirrors the package dir and <pkg>/contracts points at the contracts package. Staging under
# $MUTATION_STAGE keeps mutants/ and caches out of the bind-mounted source tree.
set -eu

MODE="${1:-}"
[ $# -gt 0 ] && shift
THRESHOLD="${MUTATION_THRESHOLD:-80}"
STAGE="${MUTATION_STAGE:-/tmp/mutation}"
# physiology last: it has by far the most mutants and the slowest tests.
PACKAGES="${MUTATION_PACKAGES:-contracts engine backend mcp_server agent physiology}"
ROOT="$(pwd)"

case "$MODE" in
    changed | full) ;;
    *) echo "usage: sh quality/mutation.sh changed [file ...] | full" >&2; exit 2 ;;
esac

stage() {
    rm -rf "$STAGE"
    mkdir -p "$STAGE/spine_monitor"
    tar -C "$ROOT" -cf - \
        --exclude=.venv --exclude=mutants --exclude=__pycache__ --exclude=.pytest_cache \
        --exclude=.hypothesis --exclude=.ruff_cache --exclude=.mypy_cache --exclude=.idea \
        --exclude=.coverage --exclude=coverage.xml . | tar -C "$STAGE/spine_monitor" -xf -
}

# run_package <pkg> [only_mutate glob ...] -> writes <pkg>/mutants/mutmut-cicd-stats.json in the stage
run_package() {
    pkg="$1"; shift
    dir="$STAGE/spine_monitor/$pkg"
    [ "$pkg" = contracts ] || ln -s ../contracts "$dir/contracts"
    also=""
    for entry in "$dir"/*; do
        name="$(basename "$entry")"
        case "$name" in src | tests | mutants | contracts | pyproject.toml) continue ;; esac
        also="$also\"$name\", "
    done
    only=""
    for f in "$@"; do only="$only\"$f\", "; done
    pytest_args="\"-p\", \"no:randomly\""
    # This test compares the source text of the generated models with the generator output, so under
    # mutmut it reads the rewritten mutants/ tree and fails the clean run. It checks text, not behaviour.
    [ "$pkg" != contracts ] || pytest_args="$pytest_args, \"--deselect\", \"tests/test_generated_models.py::test_generated_models_are_up_to_date\""
    {
        echo
        echo "[tool.mutmut]"
        echo "source_paths = [\"src\"]"
        echo "also_copy = [$also]"
        echo "pytest_add_cli_args = [$pytest_args]"
        echo "pytest_add_cli_args_test_selection = [\"tests\"]"
        echo "use_git_change_detection = false"
        [ -z "$only" ] || echo "only_mutate = [$only]"
    } >> "$dir/pyproject.toml"
    (
        cd "$dir"
        if mutmut run > mutants-run.log 2>&1; then
            mutmut export-cicd-stats > /dev/null
        elif grep -q "could not find any test case for any mutant" mutants-run.log; then
            # mutmut stops before testing when no test reaches mutated code: every mutant is a "no tests".
            n="$(python -c 'import json, pathlib; print(sum(len(json.loads(p.read_text())["exit_code_by_key"]) for p in pathlib.Path("mutants").rglob("*.py.meta")))')"
            printf '{"killed": 0, "survived": 0, "total": %s, "no_tests": %s, "skipped": 0, "suspicious": 0, "timeout": 0, "segfault": 0}\n' \
                "$n" "$n" > mutants/mutmut-cicd-stats.json
        else
            exit 1
        fi
    )
}

# report <pkg> -> prints one line, appends "detected considered" to $STAGE/totals
report() {
    python - "$1" "$STAGE/spine_monitor/$1/mutants/mutmut-cicd-stats.json" "$STAGE/totals" <<'EOF'
import json, sys
pkg, path, totals = sys.argv[1:]
s = json.load(open(path))
detected = s["killed"] + s["timeout"] + s["segfault"]
considered = s["total"] - s["skipped"]
score = f"{100 * detected / considered:5.1f} %" if considered else "  n/a  "
print(f"{pkg:<12} {score}  killed={s['killed']} survived={s['survived']} no_tests={s['no_tests']} "
      f"timeout={s['timeout']} suspicious={s['suspicious']} total={s['total']}")
open(totals, "a").write(f"{detected} {considered}\n")
EOF
}

survivors() {
    (cd "$STAGE/spine_monitor/$1" && mutmut results 2>/dev/null | sed 's/^ */    /')
}

if [ "$MODE" = full ]; then
    stage
    : > "$STAGE/totals"
    for pkg in $PACKAGES; do
        if run_package "$pkg"; then
            report "$pkg"
        else
            echo "$pkg  ERROR: mutmut failed, see $STAGE/spine_monitor/$pkg/mutants-run.log"
            tail -n 20 "$STAGE/spine_monitor/$pkg/mutants-run.log"
        fi
    done
    exit 0
fi

# changed
if [ $# -gt 0 ]; then
    files="$*"
    echo "mutation: explicit files"
else
    git_() { git -c safe.directory='*' "$@"; }
    base="${MUTATION_BASE:-}"
    if [ -z "$base" ]; then
        for ref in origin/develop develop origin/main main; do
            if git_ rev-parse --verify -q "$ref" > /dev/null; then base="$ref"; break; fi
        done
    fi
    [ -n "$base" ] || { echo "mutation: no base ref found; set MUTATION_BASE" >&2; exit 2; }
    echo "mutation: files changed in $base...HEAD"
    files="$(git_ diff --name-only --relative --diff-filter=d "$base...HEAD" -- '*/src/*.py' | tr '\n' ' ')"
fi

files="$(for f in $files; do case "$f" in */src/*.py) echo "$f" ;; esac; done)"
if [ -z "$files" ]; then
    echo "mutation: no changed */src/**/*.py files, nothing to mutate"
    exit 0
fi

stage
: > "$STAGE/totals"
for pkg in $PACKAGES; do
    globs="$(for f in $files; do case "$f" in "$pkg"/*) echo "${f#"$pkg"/}" ;; esac; done)"
    [ -n "$globs" ] || continue
    echo "$pkg:"; for g in $globs; do echo "    $g"; done
    # shellcheck disable=SC2086 # one glob per word
    if ! run_package "$pkg" $globs; then
        echo "mutation: mutmut failed for $pkg" >&2
        tail -n 40 "$STAGE/spine_monitor/$pkg/mutants-run.log" >&2
        exit 2
    fi
    report "$pkg"
    survivors "$pkg"
done

python - "$STAGE/totals" "$THRESHOLD" <<'EOF'
import sys
detected = considered = 0
for line in open(sys.argv[1]):
    d, c = map(int, line.split())
    detected += d
    considered += c
threshold = float(sys.argv[2])
if not considered:
    print("mutation: changed files produced no mutants")
    sys.exit(0)
score = 100 * detected / considered
print(f"mutation score of changed files: {score:.1f} % ({detected}/{considered}), threshold {threshold:g} %")
sys.exit(0 if score >= threshold else 1)
EOF
