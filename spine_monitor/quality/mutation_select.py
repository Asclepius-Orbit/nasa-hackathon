"""Selection and scoring of the mutants of `sh quality/mutation.sh changed` (stdlib only, run from spine_monitor/).

  select OUT PACKAGES --base REF   the functions/methods whose lines changed in REF...HEAD
  select OUT PACKAGES FILE[::NAME] ...
                                   explicit: every function of FILE, or only NAME (`func` or `Class.method`)
  score PKG SELECTION RESULTS TOTALS [--no-tests]
                                   score of the selected mutants, from `mutmut results --all true`

`select` writes OUT/<pkg> with one line per selected unit, "<file relative to pkg>\\t<module>\\t<mangled name>",
where the mangled name is mutmut's (`x_func`, `xǁClassǁmethod`) or `*` for the whole file. Exit 2 on any error.
The rules (what a unit is, how a hunk maps to units) are documented in mutation.sh.
"""

import ast
import collections
import re
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

DETECTED = {"killed", "timeout", "segfault"}
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def fail(message: str) -> NoReturn:
    print(f"mutation: ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def git(*args: str) -> str:
    done = subprocess.run(["git", "-c", "safe.directory=*", "-c", "core.quotePath=false", *args], check=False, capture_output=True, text=True)
    if done.returncode != 0:
        fail(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def units(source: str, path: str) -> list[tuple[str, int, int]]:
    """mutmut's mutation units of a module: (mangled name, first line incl. decorators, last line)."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        fail(f"{path} does not parse: {e}")
    defs = (ast.FunctionDef, ast.AsyncFunctionDef)

    def span(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int]:
        return min([node.lineno, *(d.lineno for d in node.decorator_list)]), node.end_lineno or node.lineno

    found = []
    for node in tree.body:
        if isinstance(node, defs):
            found.append((f"x_{node.name}", *span(node)))
        elif isinstance(node, ast.ClassDef):
            found.extend((f"xǁ{node.name}ǁ{m.name}", *span(m)) for m in node.body if isinstance(m, defs))
    return found


def module_of(pkg_path: str) -> str:
    """mutmut's module name for a path relative to the package dir (src/a/b.py -> a.b, src/a/__init__.py -> a)."""
    module = pkg_path.removeprefix("src/").removesuffix(".py").replace("/", ".")
    return module.removesuffix(".__init__")


def changed_lines(base: str) -> dict[str, list[tuple[int, int]]]:
    """New-side line ranges touched by each committed */src/*.py change in base...HEAD (pure deletions: (n, n + 1))."""
    diff = git("diff", "-U0", "--no-ext-diff", "--no-color", "--no-renames", "--relative", "--diff-filter=d", f"{base}...HEAD", "--", "*/src/*.py")
    ranges: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    path, in_header = None, False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path, in_header = None, True
        elif in_header and line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            in_header = False
            if not (m := HUNK.match(line)):
                fail(f"unexpected hunk header {line!r}")
            if path is None:
                fail(f"hunk without a file: {line!r}")
            start, count = int(m.group(1)), int(m.group(2) or 1)
            # count 0: lines were deleted after line `start`; the units around both sides of the cut are affected.
            ranges[path].append((start, start + 1) if count == 0 else (start, start + count - 1))
    return ranges


def select(out: Path, packages: list[str], args: list[str]) -> None:
    if args[:1] == ["--base"]:
        base = args[1]
        print(f"mutation: functions changed in {base}...HEAD")
        targets = {path: (lines, git("show", f"HEAD:./{path}")) for path, lines in changed_lines(base).items()}
    else:
        print("mutation: explicit files")
        targets = {}
        for arg in args:
            path, _, name = arg.partition("::")
            if not Path(path).is_file():
                fail(f"{path} not found")
            entry = targets.setdefault(path, ([], Path(path).read_text(encoding="utf-8")))
            entry[0].append(name)
    targets = {p: t for p, t in targets.items() if re.fullmatch(r"[^/]+/src/.+\.py", p)}
    if not targets:
        print("mutation: no changed */src/**/*.py files, nothing to mutate")
        return
    unmapped = sorted(p for p in targets if p.split("/", 1)[0] not in packages)
    if unmapped:
        print(f"mutation: WARNING: changed files outside the packages ({' '.join(packages)}):", file=sys.stderr)
        for p in unmapped:
            print(f"    {p}", file=sys.stderr)
        if len(unmapped) == len(targets):
            fail("none of the changed */src/*.py files belongs to a known package")
    out.mkdir(parents=True, exist_ok=True)
    for path, (wanted, source) in sorted(targets.items()):
        pkg, pkg_path = path.split("/", 1)
        if pkg not in packages:
            continue
        module, found = module_of(pkg_path), units(source, path)
        chosen: list[str] = []
        outside: list[str] = []
        for want in wanted:
            if isinstance(want, tuple):  # changed line range
                hit = [name for name, first, last in found if first <= want[1] and want[0] <= last]
                chosen.extend(hit)
                if not hit:
                    outside.append(f"{want[0]}" if want[0] == want[1] else f"{want[0]}-{want[1]}")
            elif not want:  # explicit file: all of it
                chosen.append("*")
            else:
                mangled = "x_" + want if "." not in want else "xǁ{}ǁ{}".format(*want.split(".", 1))
                if mangled not in {name for name, _, _ in found}:
                    fail(f"{path} has no function or method {want}")
                chosen.append(mangled)
        chosen = sorted(set(chosen))
        print(f"    {path}: {', '.join(chosen) if chosen else 'no function or method changed'}")
        if outside:
            print(f"      lines outside any function/method (mutmut never mutates them): {' '.join(outside)}")
        if chosen:
            with open(out / pkg, "a", encoding="utf-8") as f:
                f.writelines(f"{pkg_path}\t{module}\t{name}\n" for name in chosen)
    if not any(out.iterdir()):
        print("mutation: no changed function or method, nothing to mutate")


def selected(key: str, wanted: dict[str, set[str]]) -> bool:
    module, sep, rest = key.rpartition(".x")
    names = wanted.get(module)
    return bool(sep) and names is not None and ("*" in names or "x" + rest.partition("__mutmut_")[0] in names)


def score(pkg: str, selection: Path, results: Path, totals: Path, no_tests: bool) -> None:
    wanted: dict[str, set[str]] = collections.defaultdict(set)
    for line in selection.read_text(encoding="utf-8").splitlines():
        _, module, name = line.split("\t")
        wanted[module].add(name)
    by_module: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    undetected = []
    for line in results.read_text(encoding="utf-8").splitlines():
        key, sep, status = line.strip().rpartition(": ")
        module = key.rpartition(".x")[0]
        if not sep or "__mutmut_" not in key:
            continue
        if module not in wanted:
            fail(f"mutant {key} of {pkg} belongs to no selected module: module naming does not match mutmut's")
        if not selected(key, wanted):
            continue
        if status == "not checked":
            if not no_tests:
                fail(f"selected mutant {key} was not checked")
            status = "no tests"
        by_module[module][status] += 1
        if status not in DETECTED | {"skipped"}:
            undetected.append(f"{key}: {status}")
    total = sum(by_module.values(), collections.Counter())
    detected = sum(total[s] for s in DETECTED)
    considered = sum(total.values()) - total["skipped"]
    pct = f"{100 * detected / considered:5.1f} %" if considered else "  n/a  "
    print(
        f"{pkg:<12} {pct}  killed={total['killed']} survived={total['survived']} no_tests={total['no tests']} "
        f"timeout={total['timeout']} suspicious={total['suspicious']} selected={sum(total.values())}"
    )
    for module in sorted(wanted):
        c = by_module[module]
        m_considered = sum(c.values()) - c["skipped"]
        m_detected = sum(c[s] for s in DETECTED)
        m_pct = f"{100 * m_detected / m_considered:5.1f} %" if m_considered else "  n/a  "
        print(f"    {module:<50} {m_pct}  ({m_detected}/{m_considered})  {' '.join(sorted(wanted[module]))}")
    for u in undetected:
        print(f"      {u}")
    with open(totals, "a", encoding="utf-8") as f:
        f.write(f"{detected} {considered}\n")


def main(argv: list[str]) -> None:
    if len(argv) >= 4 and argv[0] == "select" and (argv[3] != "--base" or len(argv) == 5):
        select(Path(argv[1]), argv[2].split(), argv[3:])
    elif len(argv) in (5, 6) and argv[0] == "score" and argv[5:] in ([], ["--no-tests"]):
        score(argv[1], Path(argv[2]), Path(argv[3]), Path(argv[4]), argv[5:] == ["--no-tests"])
    else:
        fail(f"bad arguments {argv}; see {__file__}")


if __name__ == "__main__":
    main(sys.argv[1:])
