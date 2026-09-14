"""``helios run --tmux`` and ``--in-window`` tests (SPEC §9.1, hel-dgl item 5).

Exact tmux argv sequences and the session-creation race are tested against an
injected runner (``helios.tmux``); ``--tmux``'s preflight and output wiring
against ``helios.commands.run``; ``--in-window``'s tee-to-stdout and its
SIGHUP/SIGTERM-as-SIGINT handling against a real subprocess and real signals.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from helios import beads as beads_mod
from helios import config as config_mod
from helios import envelope as envelope_mod
from helios import run as run_mod
from helios import tmux as tmux_mod
from helios.commands import run as run_cmd

CP = subprocess.CompletedProcess


class Recorder:
    """An injected tmux runner (SPEC §9.1): records argv, replays results in order."""

    def __init__(self, results: list[subprocess.CompletedProcess]) -> None:
        self.calls: list[list[str]] = []
        self._results = list(results)

    def __call__(self, argv) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        if self._results:
            return self._results.pop(0)
        return CP(list(argv), 0)


# ------------------------------------------------------------- ensure_session


def test_ensure_session_reuses_existing() -> None:
    rec = Recorder([CP([], 0)])
    tmux_mod.ensure_session(runner=rec)
    assert rec.calls == [["tmux", "-L", "helios", "has-session", "-t", "=helios"]]


def test_ensure_session_creates_when_missing() -> None:
    rec = Recorder([CP([], 1), CP([], 0)])
    tmux_mod.ensure_session(runner=rec)
    assert rec.calls == [
        ["tmux", "-L", "helios", "has-session", "-t", "=helios"],
        ["tmux", "-L", "helios", "new-session", "-d", "-s", "helios"],
    ]


def test_ensure_session_race_recovers() -> None:
    """A failed new-session still succeeds when has-session then exits 0 (SPEC §9.1)."""
    rec = Recorder([CP([], 1), CP([], 1), CP([], 0)])
    tmux_mod.ensure_session(runner=rec)  # does not raise
    assert len(rec.calls) == 3


def test_ensure_session_failure_raises() -> None:
    rec = Recorder([CP([], 1), CP([], 1), CP([], 1)])
    with pytest.raises(RuntimeError):
        tmux_mod.ensure_session(runner=rec)


# ----------------------------------------------------------------- new_window


def test_new_window_argv_and_remain_on_exit() -> None:
    rec = Recorder([CP([], 0), CP([], 0)])
    window = tmux_mod.new_window("b1", ["--harness", "claude", "--timeout", "10"], runner=rec)
    assert window == "helios:b1"
    assert rec.calls == [
        ["tmux", "-L", "helios", "new-window", "-d", "-t", "helios:", "-n", "b1",
         "helios", "run", "b1", "--in-window", "--harness", "claude", "--timeout", "10"],
        ["tmux", "-L", "helios", "set-option", "-w", "-t", "helios:b1", "remain-on-exit", "on"],
    ]


def test_new_window_failure_raises() -> None:
    rec = Recorder([CP([], 1)])
    with pytest.raises(RuntimeError):
        tmux_mod.new_window("b1", [], runner=rec)


# -------------------------------------------------------------- launch_windows


def test_launch_windows_full_sequence_and_forwarded_args(monkeypatch) -> None:
    monkeypatch.setattr(tmux_mod.shutil, "which", lambda name: "/usr/bin/tmux")
    rec = Recorder([CP([], 0)] * 5)
    launches = tmux_mod.launch_windows(
        ["b1", "b2"], harness="claude", again=True, timeout=30, runner=rec
    )
    assert [l.bead for l in launches] == ["b1", "b2"]
    assert [l.window for l in launches] == ["helios:b1", "helios:b2"]
    assert rec.calls[0] == ["tmux", "-L", "helios", "has-session", "-t", "=helios"]
    assert rec.calls[1] == [
        "tmux", "-L", "helios", "new-window", "-d", "-t", "helios:", "-n", "b1",
        "helios", "run", "b1", "--in-window", "--harness", "claude", "--again", "--timeout", "30",
    ]
    assert rec.calls[3][:9] == [
        "tmux", "-L", "helios", "new-window", "-d", "-t", "helios:", "-n", "b2",
    ]


def test_launch_windows_missing_tmux_raises(monkeypatch) -> None:
    monkeypatch.setattr(tmux_mod.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError):
        tmux_mod.launch_windows(["b1"], harness=None, again=False, timeout=None)


# ------------------------------------------------- commands.run --tmux wiring


def make_hub(tmp_path: Path) -> Path:
    hub = tmp_path / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=hub, check=True)
    (hub / "README.md").write_text("hi\n")
    (hub / ".gitignore").write_text(".helios/\n.claude/worktrees/\n")
    (hub / "skills" / "impl").mkdir(parents=True)
    (hub / "skills" / "impl" / "SKILL.md").write_text(
        "---\nname: impl\n---\n\n# Implementation bead\n\nDo the work.\n"
    )
    (hub / "AGENTS.md").write_text(
        "# helios\n\n## Worker contract\n\nWork exactly one bead.\n\n## Orchestrator\n\nMerges.\n"
    )
    subprocess.run(["git", "add", "."], cwd=hub, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=hub, check=True)
    return hub


def make_bead(bead_id: str = "b1", **kw: object) -> beads_mod.Bead:
    fields: dict[str, object] = {"id": bead_id, "kind": "impl", "files": ["src/"], "test": "true"}
    fields.update(kw)
    return beads_mod.Bead(**fields)  # type: ignore[arg-type]


def _args(**kw: object) -> argparse.Namespace:
    base: dict[str, object] = dict(harness=None, again=False, timeout=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_commands_run_tmux_prints_lines_and_exit0(tmp_path: Path, monkeypatch, capsys) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads(
        [make_bead("b1", files=["src/a/"]), make_bead("b2", files=["src/b/"])]
    )
    cfg = config_mod.load(hub)
    monkeypatch.setattr(tmux_mod.shutil, "which", lambda name: "/usr/bin/tmux")
    rec = Recorder([CP([], 0)] * 6)
    monkeypatch.setattr(tmux_mod, "default_runner", rec)
    rc = run_cmd._run_tmux(["b1", "b2"], hub=hub, cfg=cfg, beads=beads, args=_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines() == ["b1\thelios:b1", "b2\thelios:b2"]


def test_commands_run_tmux_preflight_failure_exit2(tmp_path: Path, monkeypatch, capsys) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([beads_mod.Bead(id="b1", kind="impl", files=[], test="")])
    cfg = config_mod.load(hub)
    rc = run_cmd._run_tmux(["b1"], hub=hub, cfg=cfg, beads=beads, args=_args())
    err = capsys.readouterr().err
    assert rc == 2
    assert err.startswith("preflight: ")


def test_commands_run_tmux_missing_binary_exit2(tmp_path: Path, monkeypatch, capsys) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    cfg = config_mod.load(hub)
    monkeypatch.setattr(tmux_mod.shutil, "which", lambda name: None)
    rc = run_cmd._run_tmux(["b1"], hub=hub, cfg=cfg, beads=beads, args=_args())
    err = capsys.readouterr().err
    assert rc == 2
    assert err.startswith("helios: ")


def test_commands_run_in_window_requires_exactly_one_bead(tmp_path: Path, capsys) -> None:
    hub = make_hub(tmp_path)
    args = argparse.Namespace(
        beads=["b1", "b2"], harness=None, again=False, timeout=None, dry_run=False,
        max_parallel=3, tmux=False, in_window=True,
    )
    monkeypatch_cwd = os.getcwd()
    os.chdir(hub)
    try:
        rc = run_cmd.run(args)
    finally:
        os.chdir(monkeypatch_cwd)
    assert rc == 2
    assert "one bead" in capsys.readouterr().err


# --------------------------------------------------- --in-window real signals


CHILD_IN_WINDOW = """\
import os, sys
from pathlib import Path
from helios import beads as B, config as C, run as R
hub = Path(os.environ["HELIOS_CHILD_HUB"])
beads = B.FakeBeads([B.Bead(id="b1", kind="impl", files=["src/"], test="true")])
rc = R.run_one_in_window("b1", hub=hub, beads=beads, config=C.load(hub), harness_override="fake")
sys.exit(rc)
"""


def spawn_in_window(hub: Path, script: Path) -> subprocess.Popen:
    env = dict(os.environ, HELIOS_FAKE_SCRIPT=str(script), HELIOS_CHILD_HUB=str(hub))
    return subprocess.Popen(
        [sys.executable, "-c", CHILD_IN_WINDOW],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )


def wait_for(path: Path, needle: str = "", limit: float = 20) -> None:
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if path.exists() and needle in path.read_text():
            return
        time.sleep(0.05)
    raise TimeoutError(str(path))


def read_envelope(hub: Path, bead: str, n: int) -> dict:
    data = json.loads(
        (hub / ".helios" / "runs" / bead / f"attempt-{n}" / "envelope.json").read_text()
    )
    envelope_mod.Envelope.model_validate(data)
    return data


@pytest.mark.parametrize("sig", [signal.SIGHUP, signal.SIGTERM])
def test_in_window_hup_and_term_interrupt_like_sigint(tmp_path: Path, sig: int) -> None:
    hub = make_hub(tmp_path)
    script = tmp_path / "script.json"
    script.write_text(json.dumps(
        {"exit_code": 0, "sleep_s": 30, "stdout": "slow", "session_id": "s1",
         "report": {"status": "done", "summary": "late"}}
    ))
    proc = spawn_in_window(hub, script)
    stdout_path = hub / ".helios" / "runs" / "b1" / "attempt-1" / "stdout.jsonl"
    wait_for(stdout_path, "slow")
    os.kill(proc.pid, sig)
    out, _err = proc.communicate(timeout=30)
    assert proc.returncode == 4
    assert read_envelope(hub, "b1", 1)["execution_status"] == "interrupted"
    # --in-window tees the child's stdout bytes to its own stdout (SPEC §9.1).
    assert "slow" in out


# ------------------------------------------------- round-1-fix item 1: preflight


def test_in_window_runs_preflight_missing_memory_no_side_effects(tmp_path: Path, capsys) -> None:
    """``--in-window`` refuses a missing memory before any attempt, worktree or
    status change (round-1-fix item 1)."""
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1", memories=["ghost"])])
    cfg = config_mod.load(hub)
    rc = run_mod.run_one_in_window("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    err = capsys.readouterr().err
    assert rc == 2
    assert "ghost" in err
    assert not (hub / ".helios" / "runs" / "b1" / "attempt-1").exists()
    assert not (hub / ".claude" / "worktrees" / "b1").exists()
    assert beads.beads["b1"].status == "open"
    assert beads.argv_log == []


def test_in_window_runs_preflight_disjoint_files_not_checked_alone(tmp_path: Path, capsys) -> None:
    """``--in-window`` also catches a plain preflight failure (missing test)."""
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([beads_mod.Bead(id="b1", kind="impl", files=["src/"], test="")])
    cfg = config_mod.load(hub)
    rc = run_mod.run_one_in_window("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    err = capsys.readouterr().err
    assert rc == 2
    assert err.startswith("preflight: ")
    assert not (hub / ".helios" / "runs" / "b1").exists()


# --------------------------------------------- round-1-fix item 5: dry-run conflict


@pytest.mark.parametrize("flag", ["--tmux", "--in-window"])
def test_dry_run_with_tmux_or_in_window_refuses(tmp_path: Path, flag: str, capsys) -> None:
    hub = make_hub(tmp_path)
    args = argparse.Namespace(
        beads=["b1"], harness=None, again=False, timeout=None, dry_run=True,
        max_parallel=3, tmux=(flag == "--tmux"), in_window=(flag == "--in-window"),
    )
    cwd = os.getcwd()
    os.chdir(hub)
    try:
        rc = run_cmd.run(args)
    finally:
        os.chdir(cwd)
    err = capsys.readouterr().err
    assert rc == 2
    assert err == "helios: --dry-run cannot be combined with --tmux or --in-window\n"
    assert not (hub / ".helios").exists()


# ------------------------------------------- round-1-fix item 7: handler restore


def test_in_window_restores_previous_sighup_sigterm_handlers(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    script = tmp_path / "script.json"
    script.write_text(json.dumps(
        {"exit_code": 0, "sleep_s": 0, "stdout": "x", "session_id": "s1",
         "report": {"status": "done", "summary": "ok"}}
    ))
    monkeypatch.setenv("HELIOS_FAKE_SCRIPT", str(script))
    marker = object()

    def previous_hup(signum, frame):
        return marker

    prev = signal.signal(signal.SIGHUP, previous_hup)
    try:
        rc = run_mod.run_one_in_window(
            "b1", hub=hub, beads=beads, config=config_mod.load(hub), harness_override="fake"
        )
        assert rc == 0
        assert signal.getsignal(signal.SIGHUP) is previous_hup
    finally:
        signal.signal(signal.SIGHUP, prev)
