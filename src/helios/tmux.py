"""``helios run --tmux`` windows (SPEC §9.1).

``helios run --in-window`` itself lives in ``helios.run`` (``run_one_in_window``),
since it is the same launch pipeline with a tee-to-stdout and SIGHUP/SIGTERM
treated like SIGINT. This module only opens the tmux windows.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

TmuxRunner = Callable[[Sequence[str]], "subprocess.CompletedProcess[bytes]"]

SERVER = ("tmux", "-L", "helios")


def default_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(list(argv), capture_output=True, check=False)


@dataclass(frozen=True)
class TmuxLaunch:
    bead: str
    window: str


def ensure_session(*, runner: TmuxRunner | None = None) -> None:
    """Ensure session ``helios`` exists on the ``tmux -L helios`` server (SPEC §9.1).

    Runs ``new-session`` only when ``has-session`` fails; a failed
    ``new-session`` still counts as success when a concurrent run already won
    the race and ``has-session`` now reports the session exists.
    """
    run = runner or default_runner
    has = run([*SERVER, "has-session", "-t", "=helios"])
    if has.returncode == 0:
        return
    created = run([*SERVER, "new-session", "-d", "-s", "helios"])
    if created.returncode == 0:
        return
    recheck = run([*SERVER, "has-session", "-t", "=helios"])
    if recheck.returncode != 0:
        raise RuntimeError("tmux: failed to create session helios")


def new_window(
    bead: str, extra: Sequence[str], *, runner: TmuxRunner | None = None
) -> str:
    """Open one bead's window and set ``remain-on-exit``; return the window id (SPEC §9.1)."""
    run = runner or default_runner
    proc = run(
        [
            *SERVER, "new-window", "-d", "-t", "helios:", "-n", bead,
            "helios", "run", bead, "--in-window", *extra,
        ]
    )
    if proc.returncode != 0:
        raise RuntimeError(f"tmux: failed to open a window for {bead}")
    window = f"helios:{bead}"
    run([*SERVER, "set-option", "-w", "-t", window, "remain-on-exit", "on"])
    return window


def _forwarded_args(
    *, harness: str | None, again: bool, timeout: int | None
) -> list[str]:
    extra: list[str] = []
    if harness is not None:
        extra += ["--harness", harness]
    if again:
        extra.append("--again")
    if timeout is not None:
        extra += ["--timeout", str(timeout)]
    return extra


def launch_windows(
    beads: Sequence[str],
    *,
    harness: str | None,
    again: bool,
    timeout: int | None,
    runner: TmuxRunner | None = None,
) -> list[TmuxLaunch]:
    """``helios run --tmux``: ensure the session, open one window per bead (SPEC §9.1).

    Raises ``RuntimeError`` when the ``tmux`` binary is missing or a tmux
    call fails; the command layer turns that into exit 2.
    """
    if shutil.which("tmux") is None:
        raise RuntimeError("tmux: binary not found")
    ensure_session(runner=runner)
    extra = _forwarded_args(harness=harness, again=again, timeout=timeout)
    return [
        TmuxLaunch(bead=bead, window=new_window(bead, extra, runner=runner))
        for bead in beads
    ]
