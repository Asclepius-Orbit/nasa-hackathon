"""The parallel gate must preserve the original aggregate decision and fail closed."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("mutation_aggregate", Path(__file__).parents[1] / "quality" / "mutation_aggregate.py")
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def write_results(root, values):
    for package, value in zip(module.PACKAGES, values, strict=True):
        path = root / f"mutation-{package}" / "result.txt"
        path.parent.mkdir()
        path.write_text(value, encoding="utf-8")


def test_aggregates_before_applying_threshold(tmp_path):
    write_results(tmp_path, ["0 10", "90 90", "0 0", "0 0", "0 0", "0 0", "0 0"])
    assert module.aggregate(tmp_path) == 0
    (tmp_path / "mutation-engine" / "result.txt").write_text("60 90", encoding="utf-8")
    assert module.aggregate(tmp_path) == 1


def test_minimum_sample_and_zero(tmp_path):
    write_results(tmp_path, ["0 9", "0 0", "0 0", "0 0", "0 0", "0 0", "0 0"])
    assert module.aggregate(tmp_path) == 0
    (tmp_path / "mutation-contracts" / "result.txt").write_text("0 0", encoding="utf-8")
    assert module.aggregate(tmp_path) == 0


def test_missing_or_invalid_shard_fails_closed(tmp_path):
    write_results(tmp_path, ["0 0"] * len(module.PACKAGES))
    result = tmp_path / "mutation-agent" / "result.txt"
    for invalid in ("", "1", "2 1", "-1 1", "0 0 0", "bad data"):
        result.write_text(invalid, encoding="utf-8")
        assert module.aggregate(tmp_path) == 2
    result.unlink()
    assert module.aggregate(tmp_path) == 2
