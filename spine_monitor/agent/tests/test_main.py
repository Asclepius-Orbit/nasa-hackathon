import pytest
import spine_agent
from spine_agent import cli


@pytest.mark.unit
def test_main_returns_the_exit_code_of_the_cli(monkeypatch: pytest.MonkeyPatch):
    seen: list[list[str] | None] = []

    async def fake_amain(argv: list[str] | None = None) -> int:
        seen.append(argv)
        return 0

    monkeypatch.setattr(cli, "amain", fake_amain)
    assert spine_agent.main(["--astronaut", "AST-01"]) == 0
    assert seen == [["--astronaut", "AST-01"]]


@pytest.mark.unit
def test_main_returns_130_on_ctrl_c_without_a_traceback(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    async def interrupted(argv: list[str] | None = None) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "amain", interrupted)
    assert spine_agent.main(["--astronaut", "AST-01"]) == 130
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.unit
def test_main_returns_2_when_the_astronaut_is_missing(capsys: pytest.CaptureFixture[str]):
    assert spine_agent.main([]) == 2
    assert "--astronaut" in capsys.readouterr().err
