#!/bin/sh
# Mutation testing with mutmut, one workspace package at a time.
#
#   sh quality/mutation.sh changed [file ...]   score of the */src/**/*.py files changed vs the base;
#                                               exit 1 when below MUTATION_THRESHOLD (80 %), 0 when nothing changed,
#                                               2 on any infrastructure error (fail closed).
#   sh quality/mutation.sh full                 score per package; always exits 0 (errors are warnings on stderr).
#
# Run from spine_monitor/. Explicit files (paths relative to spine_monitor/) replace the git diff in `changed`.
# Base for `changed`: $MUTATION_BASE, else the first of origin/develop, develop, origin/main, main that exists.
# Only committed changes count (git diff base...HEAD): on a Windows checkout the container's git would see
# every CRLF file in the working tree as modified.
#
# The `changed` score is AGGREGATE over all changed files, not per file: per-file scores are printed for
# visibility, but gating each file would block every PR that touches a large, weakly-killed module
# (physiology) until that whole module reaches 80 %.
#
# Why a staged copy: mutmut names mutants after the file path minus a leading "src/", and only puts
# mutants/, mutants/src and mutants/source on sys.path. So it must run with the package dir as cwd and
# source_paths = ["src"]. The tests locate fixtures with Path(__file__).parents[N] (../configs, ../../contracts),
# so mutants/ mirrors the package dir and <pkg>/contracts points at the contracts package. Staging under
# $MUTATION_STAGE keeps mutants/ and caches out of the bind-mounted source tree.
# The staged pyproject.toml of each package gets a generated [tool.mutmut] appended, so a package must not
# define its own [tool.mutmut] table (TOML forbids the duplicate).
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

# Infrastructure error: fatal (exit 2) in `changed`, a warning in `full`.
infra_error() {
    echo "mutation: ERROR: $*" >&2
    [ "$MODE" = full ] || exit 2
}

stage() {
    rm -rf "$STAGE" &&
        mkdir -p "$STAGE/spine_monitor" &&
        tar -C "$ROOT" -cf - \
            --exclude=.venv --exclude=mutants --exclude=__pycache__ --exclude=.pytest_cache \
            --exclude=.hypothesis --exclude=.ruff_cache --exclude=.mypy_cache --exclude=.idea \
            --exclude=.coverage --exclude=coverage.xml . | tar -C "$STAGE/spine_monitor" -xf - &&
        : > "$STAGE/totals"
}

# run_package <pkg> [only_mutate glob ...] -> writes <pkg>/mutants/mutmut-cicd-stats.json in the stage.
# Returns non-zero on any failure (callers decide whether it is fatal).
run_package() {
    pkg="$1"; shift
    dir="$STAGE/spine_monitor/$pkg"
    [ -d "$dir/src" ] || { echo "mutation: $dir/src not found" >&2; return 1; }
    if [ "$pkg" != contracts ]; then ln -s ../contracts "$dir/contracts" || return 1; fi
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
    } >> "$dir/pyproject.toml" || return 1
    (
        cd "$dir" || exit 1
        if mutmut run > mutants-run.log 2>&1; then
            mutmut export-cicd-stats > /dev/null || exit 1
        elif grep -q "could not find any test case for any mutant" mutants-run.log; then
            # mutmut stops before testing when no test reaches mutated code: every mutant is a "no tests".
            n="$(python -c 'import json, pathlib; print(sum(len(json.loads(p.read_text())["exit_code_by_key"]) for p in pathlib.Path("mutants").rglob("*.py.meta")))')" || n=""
            case "$n" in '' | *[!0-9]*) echo "mutation: could not count the mutants of $pkg" >&2; exit 1 ;; esac
            printf '{"killed": 0, "survived": 0, "total": %s, "no_tests": %s, "skipped": 0, "suspicious": 0, "timeout": 0, "segfault": 0}\n' \
                "$n" "$n" > mutants/mutmut-cicd-stats.json || exit 1
        else
            exit 1
        fi
    )
}

# report <pkg> -> prints the package line, appends "detected considered" to $STAGE/totals
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
with open(totals, "a") as f:
    f.write(f"{detected} {considered}\n")
EOF
}

# per_file <pkg> -> score per mutated module, then the mutants not killed
per_file() {
    (cd "$STAGE/spine_monitor/$1" && mutmut results --all true 2> /dev/null) | python -c '
import collections, re, sys
DETECTED = {"killed", "timeout", "segfault"}
by_module = collections.defaultdict(collections.Counter)
undetected = []
for line in sys.stdin:
    name, sep, status = line.strip().rpartition(": ")
    m = re.match(r"(.*?)\.x[_ǁ]", name)
    if not sep or not m:
        continue
    by_module[m.group(1)][status] += 1
    if status not in DETECTED | {"skipped"}:
        undetected.append(f"{name}: {status}")
for module, c in sorted(by_module.items()):
    considered = sum(c.values()) - c["skipped"]
    detected = sum(c[s] for s in DETECTED)
    score = f"{100 * detected / considered:5.1f} %" if considered else "  n/a  "
    print(f"    {module:<50} {score}  ({detected}/{considered})")
for u in undetected:
    print(f"      {u}")
'
}

if [ "$MODE" = full ]; then
    if ! stage; then
        infra_error "could not stage $ROOT into $STAGE"
        exit 0
    fi
    for pkg in $PACKAGES; do
        if run_package "$pkg" && report "$pkg"; then
            continue
        fi
        infra_error "mutmut failed for $pkg, see $STAGE/spine_monitor/$pkg/mutants-run.log"
        tail -n 20 "$STAGE/spine_monitor/$pkg/mutants-run.log" >&2 2> /dev/null || true
    done
    exit 0
fi

# changed
if [ $# -gt 0 ]; then
    candidates="$*"
    echo "mutation: explicit files"
else
    git_() { git -c safe.directory='*' "$@"; }
    base="${MUTATION_BASE:-}"
    if [ -z "$base" ]; then
        for ref in origin/develop develop origin/main main; do
            if git_ rev-parse --verify -q "$ref" > /dev/null; then base="$ref"; break; fi
        done
        [ -n "$base" ] || infra_error "no base ref found (origin/develop, develop, origin/main, main); set MUTATION_BASE"
    fi
    git_ rev-parse --verify -q "$base^{commit}" > /dev/null || infra_error "base '$base' does not resolve to a commit"
    git_ merge-base "$base" HEAD > /dev/null || infra_error "no merge-base between '$base' and HEAD (shallow checkout?)"
    echo "mutation: files changed in $base...HEAD"
    candidates="$(git_ diff --name-only --relative --diff-filter=d "$base...HEAD" -- '*/src/*.py')" ||
        infra_error "git diff against '$base' failed"
fi

files=""
for f in $candidates; do case "$f" in */src/*.py) files="$files $f" ;; esac; done
if [ -z "$files" ]; then
    echo "mutation: no changed */src/**/*.py files, nothing to mutate"
    exit 0
fi

unmapped=""
for f in $files; do
    known=""
    for pkg in $PACKAGES; do case "$f" in "$pkg"/src/*) known=1 ;; esac; done
    [ -n "$known" ] || unmapped="$unmapped $f"
done
if [ -n "$unmapped" ]; then
    echo "mutation: WARNING: changed files outside the packages ($PACKAGES):" >&2
    for f in $unmapped; do echo "    $f" >&2; done
    [ "$unmapped" != "$files" ] || infra_error "none of the changed */src/*.py files belongs to a known package"
fi

stage || infra_error "could not stage $ROOT into $STAGE"
for pkg in $PACKAGES; do
    globs="$(for f in $files; do case "$f" in "$pkg"/src/*) echo "${f#"$pkg"/}" ;; esac; done)"
    [ -n "$globs" ] || continue
    echo "$pkg:"; for g in $globs; do echo "    $g"; done
    # shellcheck disable=SC2086 # one glob per word
    if ! run_package "$pkg" $globs; then
        tail -n 40 "$STAGE/spine_monitor/$pkg/mutants-run.log" >&2 2> /dev/null || true
        infra_error "mutmut failed for $pkg"
    fi
    report "$pkg" || infra_error "could not read the mutmut stats of $pkg"
    per_file "$pkg" || echo "mutation: WARNING: could not list per-file results of $pkg" >&2
done

python - "$STAGE/totals" "$THRESHOLD" <<'EOF' || exit $?
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
print(f"mutation score of changed files (aggregate): {score:.1f} % ({detected}/{considered}), threshold {threshold:g} %")
sys.exit(0 if score >= threshold else 1)
EOF
