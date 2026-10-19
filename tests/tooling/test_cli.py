"""protocol_emulator.cli is the installed console-script entry point."""
import protocol_emulator.cli
import protocol_emulator.v3 as v3
import pytest

from protocol_emulator.cli import main


def test_cli_imports() -> None:
    assert protocol_emulator.cli.main is main


def test_version_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == v3.__version__


def test_missing_subcommand_exits_with_code_2() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
