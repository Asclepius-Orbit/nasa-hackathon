#!/bin/sh
# Mutation testing with mutmut, one workspace package at a time.
#
#   sh quality/mutation.sh changed [file[::name] ...]
#                                   score of the functions and methods changed vs the base, aggregated. Exit codes:
#                                     0  score >= MUTATION_THRESHOLD (80 %), or nothing to mutate, or the score is below
#                                        it but fewer than MUTATION_MIN_SAMPLE (10) mutants were scored (see below)
#                                     1  score below the threshold with at least MUTATION_MIN_SAMPLE mutants scored
#                                     2  any infrastructure error (fail closed; never masked by the minimum-sample rule)
#   sh quality/mutation.sh full     score per package; always exits 0 (errors are warnings on stderr).
#
# Run from spine_monitor/. Explicit arguments (paths relative to spine_monitor/) replace the git diff in `changed`:
# `file` is every function of the file, `file::func` or `file::Class.method` only that one; a file that is not a
# <package>/src/**/*.py of the packages (./ and absolute paths under spine_monitor/ are normalised) exits 2.
# Base for `changed`: $MUTATION_BASE, else the first of origin/develop, develop, origin/main, main that exists.
# Only committed changes count (git diff base...HEAD): on a Windows checkout the container's git would see
# every CRLF file in the working tree as modified. mutmut still mutates the working tree, so commit first.
#
# What `changed` mutates (quality/mutation_select.py; checked against mutmut 3.8.1's source):
# - mutmut's unit is a top-level def/async def or a def directly in a top-level class; nested defs, lambdas and inner
#   classes belong to their unit. mutmut never mutates module-level code, class attributes, methods of nested classes
#   or functions with decorators other than a lone @staticmethod/@classmethod, not even in `full`.
# - The new-side lines of `git diff -U0 --no-renames base...HEAD` are mapped with Python's ast, on the HEAD version, to
#   the units they overlap (a unit spans its decorators to its last line; a pure deletion touches the lines on both
#   sides of the cut). A renamed function is selected under its new name; a renamed or new file is all new lines.
#   Changed lines outside any unit are listed and add nothing: mutmut has no mutant for them, and mutating the whole
#   module instead would only re-test functions the change did not touch.
# - A changed file that does not parse fails closed: mutmut would copy it unmutated with only a warning.
# - mutmut runs only `<module>.<x_func|xǁClassǁmethod>__mutmut_*` (module = path under src/ with dots, `.__init__`
#   dropped, as mutmut names it) and the score counts only those mutants, read from `mutmut results`; a selected mutant
#   left unchecked is an error. Each mutant gets the same tests as in a whole-file run, so the cost scales with the
#   changed functions, plus a fixed cost per package: mutmut's stats run (whole package suite under coverage) and its
#   clean run of the whole suite (kept as in a whole-file run; not narrowed to the tests of the selected units).
# - Minimum sample (decided by Uri on 2026-11-04): when `changed` scores fewer than MUTATION_MIN_SAMPLE mutants (default
#   10; 0 disables the rule; the count is the denominator of the aggregate score, skipped mutants excluded), a score
#   below the threshold is printed together with a notice "score shown, not enforced" and the exit code is 0, not 1.
#   This is a conscious decision: a change that selects fewer than 10 mutants can pass the gate without its tests
#   killing them. Reason: one mutant that flips between identical runs (about 3 of 385 in stretch.py is measured) moves
#   the score by 100/N points for N scored mutants, so on a handful of mutants the score is noise. The rule only turns
#   the below-threshold exit 1 into 0; it never touches exit 2, nor a score at or above the threshold. N is the
#   AGGREGATE denominator over all selected packages, and the rule also applies in explicit-file mode.
#   MUTATION_MIN_SAMPLE must be a non-negative integer and MUTATION_THRESHOLD a non-negative number (80, 80.5); both
#   are validated before any work and a bad value is a usage error (exit 2) in `changed` and `full` alike.
#
# The score is AGGREGATE over all selected functions, not per file: per-module scores are printed for
# visibility, but gating each one would block every PR that touches a weakly-killed function until it reaches 80 %.
#
# Why a staged copy: mutmut names mutants after the file path minus a leading "src/", and only puts
# mutants/, mutants/src and mutants/source on sys.path. So it must run with the package dir as cwd and
# source_paths = ["src"]. The tests locate fixtures with Path(__file__).parents[N] (../configs, ../../contracts),
# so mutants/ mirrors the package dir and <pkg>/contracts points at the contracts package. Staging under
# $MUTATION_STAGE keeps mutants/ and caches out of the bind-mounted source tree.
# The staged pyproject.toml of each package gets a generated [tool.mutmut] appended, so a package must not
# define its own [tool.mutmut] table (TOML forbids the duplicate).
#
# Logging calls are not mutated (`full` and `changed` alike): mutants of the arguments of `_log.warning("msg %s", x)`
# change no behaviour the tests observe and would only count against the 80 % gate. The generated config sets mutmut's
# `do_not_mutate_patterns` (a regex applied to every source line; the expressions that start on a matching line are
# skipped, children included) to LOG_CALL below: a statement that starts a call to debug/info/warning/warn/error/
# exception/critical/log on a `log`, `_log`, `logger`, `_logger` or `logging` object (optionally `self.`-prefixed).
# Nothing else is skipped and the production code carries no `# pragma: no mutate`. Calls that do not start their line
# (`x = log.info(...)`, `if c: log.info(...)`) and `logging.basicConfig(...)` are still mutated. Both the skipped
# mutants and the score are those mutmut 3.8.1 reports: skipped statements simply do not exist for it.
set -eu

MODE="${1:-}"
[ $# -gt 0 ] && shift
THRESHOLD="${MUTATION_THRESHOLD:-80}"
MIN_SAMPLE="${MUTATION_MIN_SAMPLE:-10}"
STAGE="${MUTATION_STAGE:-/tmp/mutation}"
# physiology last: it has by far the most mutants and the slowest tests.
PACKAGES="${MUTATION_PACKAGES:-contracts engine backend mcp_server agent physiology gateway}"
ROOT="$(pwd)"
NAMES=""
# TOML literal string (single quotes): the backslashes reach mutmut untouched.
LOG_CALL='^\s*(?:self\.)?_?(?:log|logger|logging)\.(?:debug|info|warning|warn|error|exception|critical|log)\('

case "$MODE" in
    changed | full) ;;
    *) echo "usage: sh quality/mutation.sh changed [file[::name] ...] | full" >&2; exit 2 ;;
esac
case "$MIN_SAMPLE" in
    '' | *[!0-9]*) echo "mutation: ERROR: MUTATION_MIN_SAMPLE must be a non-negative integer, got '$MIN_SAMPLE'" >&2; exit 2 ;;
esac
case "$THRESHOLD" in
    '' | . | *[!0-9.]* | *.*.* | .* | *.) echo "mutation: ERROR: MUTATION_THRESHOLD must be a non-negative number like 80 or 80.5, got '$THRESHOLD'" >&2; exit 2 ;;
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

# run_package <pkg> [only_mutate glob ...] -> runs mutmut in the stage. With $NAMES empty (full) it writes
# <pkg>/mutants/mutmut-cicd-stats.json; with $NAMES (changed) it runs only the mutants matching those names and the
# caller scores them from `mutmut results` (mutants-no-tests marks "no test reaches any mutant of the package").
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
    # mutmut reruns each mutant's tests by node id, and pytest cannot address the Schemathesis-generated ids of this
    # file (`...[POST /assess]`: exit 4), which mutmut records as a kill: every mutant the fuzz test reached was a fake
    # kill. Ignored under mutmut; it still runs in the normal suite. Every other id of every package is addressable.
    [ "$pkg" != engine ] || pytest_args="$pytest_args, \"--ignore\", \"tests/test_contract_openapi_fuzz.py\""
    {
        echo
        echo "[tool.mutmut]"
        echo "source_paths = [\"src\"]"
        echo "also_copy = [$also]"
        echo "pytest_add_cli_args = [$pytest_args]"
        echo "pytest_add_cli_args_test_selection = [\"tests\"]"
        echo "use_git_change_detection = false"
        printf '%s\n' "do_not_mutate_patterns = ['$LOG_CALL']"
        [ -z "$only" ] || echo "only_mutate = [$only]"
    } >> "$dir/pyproject.toml" || return 1
    (
        cd "$dir" || exit 1
        set -f # $NAMES holds mutant-name globs for mutmut, not for the shell
        # shellcheck disable=SC2086 # one name per word
        if mutmut run $NAMES > mutants-run.log 2>&1; then
            [ -n "$NAMES" ] || mutmut export-cicd-stats > /dev/null || exit 1
        elif [ -n "$NAMES" ] && grep -q "could not find any test case for any mutant" mutants-run.log; then
            : > mutants-no-tests
        elif [ -n "$NAMES" ] && grep -q "Filtered for specific mutants, but nothing matches" mutants-run.log; then
            : # none of the selected functions has a mutant; the score step counts zero
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
sel="$STAGE.select"
rm -rf "$sel" || infra_error "could not clear $sel"
if [ $# -gt 0 ]; then
    python quality/mutation_select.py select "$sel" "$PACKAGES" "$@" || infra_error "could not select the mutants of $*"
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
    python quality/mutation_select.py select "$sel" "$PACKAGES" --base "$base" ||
        infra_error "could not select the functions changed in $base...HEAD"
fi
[ -n "$(ls "$sel" 2> /dev/null)" ] || exit 0 # the selection step printed why

stage || infra_error "could not stage $ROOT into $STAGE"
for pkg in $PACKAGES; do
    [ -s "$sel/$pkg" ] || continue
    files="$(cut -f1 "$sel/$pkg" | sort -u)"
    # <module>.<mangled>__mutmut_* per unit (<module>.x*__mutmut_* for a whole file). Every name contains __mutmut_, so
    # none matches a function name and mutmut's clean run keeps the whole suite, exactly as in a whole-file run.
    NAMES="$(awk -F '\t' '{ print $2 "." ($3 == "*" ? "x*" : $3) "__mutmut_*" }' "$sel/$pkg")"
    echo "$pkg:"; for f in $files; do echo "    $f"; done
    # shellcheck disable=SC2086 # one glob per word
    if ! run_package "$pkg" $files; then
        tail -n 40 "$STAGE/spine_monitor/$pkg/mutants-run.log" >&2 2> /dev/null || true
        infra_error "mutmut failed for $pkg"
    fi
    dir="$STAGE/spine_monitor/$pkg"
    (cd "$dir" && mutmut results --all true > mutants-results.txt 2> /dev/null) || infra_error "could not list the mutmut results of $pkg"
    no_tests=""
    [ ! -e "$dir/mutants-no-tests" ] || no_tests="--no-tests"
    # shellcheck disable=SC2086 # empty or one flag
    python quality/mutation_select.py score "$pkg" "$sel/$pkg" "$dir/mutants-results.txt" "$STAGE/totals" $no_tests ||
        infra_error "could not score the mutants of $pkg"
done

python - "$STAGE/totals" "$THRESHOLD" "$MIN_SAMPLE" <<'EOF' || exit $?
import sys
detected = considered = 0
for line in open(sys.argv[1]):
    d, c = map(int, line.split())
    detected += d
    considered += c
threshold = float(sys.argv[2])
min_sample = int(sys.argv[3])
if not considered:
    print("mutation: the changed functions produced no mutants")
    sys.exit(0)
score = 100 * detected / considered
print(f"mutation score of changed functions (aggregate): {score:.1f} % ({detected}/{considered}), threshold {threshold:g} %")
if score >= threshold:
    sys.exit(0)
if considered < min_sample:
    print(f"mutation: only {considered} mutants selected (< {min_sample}): score shown, not enforced")
    sys.exit(0)
sys.exit(1)
EOF
