"""Combine all package results, failing closed if a shard is missing or malformed."""
import sys
from pathlib import Path

PACKAGES = ("contracts", "engine", "backend", "mcp_server", "agent", "physiology", "gateway")


def aggregate(directory: Path, threshold: float = 80, minimum: int = 10) -> int:
    detected = considered = 0
    for package in PACKAGES:
        path = directory / f"mutation-{package}" / "result.txt"
        try:
            parts = path.read_text(encoding="utf-8").split()
            if len(parts) != 2 or any(not value.isascii() or not value.isdecimal() for value in parts):
                raise ValueError("expected two non-negative integers")
            found, total = map(int, parts)
            if found > total:
                raise ValueError("detected exceeds considered")
        except (OSError, ValueError) as exc:
            print(f"mutation: ERROR: invalid result for {package}: {exc}", file=sys.stderr)
            return 2
        detected += found
        considered += total
    if not considered:
        print("mutation: the changed functions produced no mutants")
        return 0
    score = 100 * detected / considered
    print(f"mutation score of changed functions (aggregate): {score:.1f} % ({detected}/{considered}), threshold {threshold:g} %")
    if score >= threshold:
        return 0
    if considered < minimum:
        print(f"mutation: only {considered} mutants selected (< {minimum}): score shown, not enforced")
        return 0
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: mutation_aggregate.py ARTIFACT_DIRECTORY")
    sys.exit(aggregate(Path(sys.argv[1])))
