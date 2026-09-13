from types import SimpleNamespace

import pytest

from helios import cli


def fake(name: str, calls: list[str]) -> SimpleNamespace:
    def add_arguments(parser):
        parser.add_argument("--flag", action="store_true")

    def run(args):
        calls.append(f"{name}:{args.flag}")
        return 0

    return SimpleNamespace(__name__=f"fake.{name}", NAME=name, HELP=name, add_arguments=add_arguments, run=run)


def test_nested_discovery_dispatches():
    calls: list[str] = []
    mods = [fake("run", calls), fake("unit new", calls), fake("unit run", calls)]
    parser = cli.build_parser(mods)
    args = parser.parse_args(["unit", "run", "--flag"])
    assert args._run(args) == 0
    assert calls == ["unit run:True"]


def test_command_and_group_conflict():
    with pytest.raises(ValueError):
        cli.build_parser([fake("unit new", []), fake("unit", [])])


def test_real_package_builds():
    cli.build_parser()


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 2
    assert "helios" in capsys.readouterr().out


def test_version_prints_version(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out == "helios 0.1.0\n"
