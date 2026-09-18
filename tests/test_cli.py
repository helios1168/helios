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


# A bad .agents/workflow.toml must reach the user as a refusal, never a traceback (SPEC §2.3,
# §5). Each case pairs the file body with the dotted key the message has to name.
BAD_CONFIGS = {
    "unknown-section": ("nonesuch = 1\n", "nonesuch"),
    "unknown-nested-key": ("[project]\nnonesuch = 1\n", "project.nonesuch"),
    "wrong-type": ("[project]\ntest = 1\n", "project.test"),
    "telemetry-without-endpoint": ("[telemetry]\nenabled = true\n", "telemetry.endpoint"),
}

# Every command module that loads the configuration, with the arguments its parser requires.
CONFIG_COMMANDS = [
    ["run", "hel-abc"],
    ["unit", "new", "u1", "Title", "--stages", "code"],
    ["claims", "check"],
    ["claims", "attack", "c1"],
    ["program", "check"],
    ["program", "show"],
    ["program", "diff", "HEAD~1", "HEAD"],
    ["resume", "hel-abc"],
    ["say", "hel-abc", "text"],
    ["stop", "hel-abc"],
    ["attach", "hel-abc"],
    ["ps"],
]


def write_hub(tmp_path, body: str, monkeypatch):
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "workflow.toml").write_text(body)
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize("case", sorted(BAD_CONFIGS))
def test_run_refuses_a_bad_config(case, tmp_path, monkeypatch, capsys):
    body, dotted = BAD_CONFIGS[case]
    write_hub(tmp_path, body, monkeypatch)
    assert cli.main(["run", "hel-abc"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("helios: ")
    assert dotted in err
    assert "Traceback" not in err


@pytest.mark.parametrize("argv", CONFIG_COMMANDS, ids=lambda a: "-".join(a[:2]))
def test_every_config_command_refuses_a_bad_config(argv, tmp_path, monkeypatch, capsys):
    body, dotted = BAD_CONFIGS["unknown-nested-key"]
    write_hub(tmp_path, body, monkeypatch)
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert dotted in err
    for line in err.splitlines():
        assert line.startswith("helios: ")
