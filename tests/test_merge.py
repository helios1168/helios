"""Merge command tests (SPEC section 12)."""

from __future__ import annotations

import json
import subprocess
import fcntl
import shutil
import unicodedata
from argparse import Namespace
from pathlib import Path

import pytest

from helios import run as run_mod
from helios.beads import Bead, Beads, FakeBeads
from helios.config import Config, ProjectConfig
from helios.envelope import AgentReport, Envelope, ExecutionStatus, WorkStatus
from helios.merge import MergeError, merge_bead


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)
    return proc.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    hub = tmp_path / "repo"
    hub.mkdir(parents=True)
    _git(hub, "init", "-b", "main")
    _git(hub, "config", "user.email", "test@example.com")
    _git(hub, "config", "user.name", "Test")
    (hub / "value.txt").write_text("base\n")
    (hub / ".beads").mkdir()
    (hub / ".beads" / "issues.jsonl").write_text("{}\n")
    _git(hub, "add", ".")
    _git(hub, "commit", "-m", "base")
    worktree = tmp_path / "worktree" / "b1"
    worktree.parent.mkdir()
    _git(hub, "worktree", "add", "-b", "worktree-b1", str(worktree), "main")
    _git(hub, "worktree", "lock", "--reason", "keep", str(worktree))
    (worktree / "value.txt").write_text("merged\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "change")
    return hub, worktree


def _beads(hub: Path, worktree: Path, *, real_hashes: bool = False) -> FakeBeads:
    """FakeBeads with a closed impl bead "b1" and a verified verify bead "v1".

    Most tests here exercise merge mechanics (rebase, push, worktree
    removal...), not evidence hashing, and inject a fake `input_hashes`
    through `_run` regardless of what is recorded, so the recorded
    `input_hashes` default to `{}` (matching that fake's own `{}`). A test
    that calls `merge_bead` or `helios.commands.merge` without an injected
    `input_hashes` exercises the real default (hel-z3b,
    `run.recompute_input_hashes`) and needs `real_hashes=True` so the
    recorded value actually matches: nothing here gives the verify bead
    docs, memories or a unit file, so that is the hash of its bead JSON
    alone.
    """
    impl = Bead(
        id="b1",
        kind="impl",
        unit="u1",
        status="closed",
        metadata={"output_commit": _git(worktree, "rev-parse", "HEAD")},
    )
    verify = Bead(
        id="v1",
        kind="verify-code",
        unit="u1",
        parent="b1",
        status="closed",
        metadata={"verdict": "verified", "attempt": 1},
        labels=["unit:u1"],
    )
    beads = FakeBeads([impl, verify])
    hashes: dict[str, str] = {}
    if real_hashes:
        hashes = run_mod.recompute_input_hashes(hub, Config(hub=hub, project=_project()), beads, verify)
    envelope_dir = hub / ".helios" / "runs" / "v1" / "attempt-1"
    envelope_dir.mkdir(parents=True)
    (envelope_dir / "envelope.json").write_text(
        Envelope(
            task_id="v1",
            attempt=1,
            attempt_id="v1#1",
            kind="verify-code",
            harness="fake",
            started_at="now",
            base_commit=impl.metadata["output_commit"],
            input_hashes=hashes,
            execution_status=ExecutionStatus.COMPLETED,
            report=AgentReport(status=WorkStatus.DONE, summary="ok"),
        ).model_dump_json()
    )
    return beads


def _project() -> ProjectConfig:
    return ProjectConfig(worktrees="../worktree")


def _run(beads: FakeBeads, hub: Path, *, runner=None, hashes=None, dry_run=False) -> tuple[int, str]:
    return merge_bead(
        hub,
        "b1",
        project=_project(),
        beads=beads,
        check_runner=runner or (lambda _command, _cwd: 0),
        input_hashes=hashes or (lambda _bead: {}),
        dry_run=dry_run,
    )


def _new_envelope(hub: Path, worktree: Path, *, hashes: dict[str, str] | None = None) -> None:
    output = _git(worktree, "rev-parse", "HEAD")
    path = hub / ".helios" / "runs" / "v1" / "attempt-1"
    path.mkdir(parents=True, exist_ok=True)
    (path / "envelope.json").write_text(
        Envelope(
            task_id="v1",
            attempt=1,
            attempt_id="v1#1",
            kind="verify-code",
            harness="fake",
            started_at="now",
            base_commit=output,
            input_hashes=hashes or {},
            execution_status=ExecutionStatus.COMPLETED,
            report=AgentReport(status=WorkStatus.DONE, summary="ok"),
        ).model_dump_json()
    )


def _assert_refusal(beads: FakeBeads, hub: Path, fn) -> None:
    before = _git(hub, "rev-parse", "main")
    with pytest.raises(MergeError) as error:
        fn()
    assert error.value.code == 2
    assert beads.argv_log == []
    assert _git(hub, "rev-parse", "main") == before


def test_refuses_missing_envelope_without_bead_writes(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = FakeBeads(
        [
            Bead(id="b1", unit="u1", status="closed", metadata={"output_commit": "x"}),
            Bead(
                id="v1",
                kind="verify-code",
                unit="u1",
                parent="b1",
                status="closed",
                metadata={"verdict": "verified", "attempt": 1},
            ),
        ]
    )
    with pytest.raises(MergeError) as error:
        merge_bead(hub, "b1", project=ProjectConfig(worktrees="../worktree"), beads=beads)
    assert error.value.code == 2
    assert beads.argv_log == []


def test_dry_run_does_not_write_beads(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree, real_hashes=True)
    assert merge_bead(
        hub,
        "b1",
        project=ProjectConfig(worktrees="../worktree"),
        beads=beads,
        dry_run=True,
    ) == (0, "would rebase, test, merge, push, and remove")
    assert beads.argv_log == []


def test_happy_path_merges_and_removes_worktree(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree, real_hashes=True)
    assert _git(hub, "status", "--porcelain", "--untracked-files=no") == ""
    assert _git(worktree, "status", "--porcelain", "--untracked-files=no") == ""
    code, message = merge_bead(
        hub,
        "b1",
        project=ProjectConfig(worktrees="../worktree"),
        beads=beads,
        check_runner=lambda _command, _cwd: 0,
    )
    assert (code, message) == (0, "merged")
    assert not worktree.exists()
    assert _git(hub, "show", "main:value.txt") == "merged"
    assert beads.beads["b1"].metadata["merge_commit"] == _git(hub, "rev-parse", "HEAD")


@pytest.mark.parametrize("case", ["verify-open", "bad-verdict", "no-verify", "missing-envelope", "impl-open"])
def test_each_step_one_refusal_is_exit_two_and_writes_nothing(tmp_path: Path, case: str) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    if case == "verify-open":
        beads.beads["v1"].status = "open"
    elif case == "bad-verdict":
        beads.beads["v1"].metadata["verdict"] = "inconclusive"
    elif case == "no-verify":
        beads.beads.pop("v1")
    elif case == "missing-envelope":
        (hub / ".helios" / "runs" / "v1" / "attempt-1" / "envelope.json").unlink()
    elif case == "impl-open":
        beads.beads["b1"].status = "open"
    _assert_refusal(beads, hub, lambda: _run(beads, hub))


@pytest.mark.parametrize("missing", ["x", "y"])
def test_stale_hash_missing_key_is_exit_two(tmp_path: Path, missing: str) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    _new_envelope(hub, worktree, hashes={"x": "old"} if missing == "x" else {"y": "old"})
    current = {"y": "new"} if missing == "x" else {"x": "new"}
    _assert_refusal(beads, hub, lambda: _run(beads, hub, hashes=lambda _bead: current))


def test_stale_base_commit_is_exit_two(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    envelope = hub / ".helios" / "runs" / "v1" / "attempt-1" / "envelope.json"
    payload = Envelope.model_validate_json(envelope.read_text()).model_copy(update={"base_commit": "wrong"})
    envelope.write_text(payload.model_dump_json())
    _assert_refusal(beads, hub, lambda: _run(beads, hub))


def test_stale_changed_non_prompt_hash_is_exit_two(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    _new_envelope(hub, worktree, hashes={"bead": "old"})
    _assert_refusal(beads, hub, lambda: _run(beads, hub, hashes=lambda _bead: {"bead": "new"}))


@pytest.mark.parametrize("dirty", ["hub", "worktree"])
def test_dirty_tree_is_exit_two_and_unchanged(tmp_path: Path, dirty: str) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    (hub if dirty == "hub" else worktree).joinpath("value.txt").write_text("dirty\n")
    _assert_refusal(beads, hub, lambda: _run(beads, hub))


def test_non_main_hub_is_exit_two(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    _git(hub, "checkout", "-b", "other")
    _assert_refusal(beads, hub, lambda: _run(beads, hub))


def test_held_lock_is_exit_two(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    lock_path = hub / ".helios" / "runs" / "b1" / "lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _assert_refusal(beads, hub, lambda: _run(beads, hub))


def test_refusal_never_unlinks_the_lock_file(tmp_path: Path) -> None:
    """SPEC 12 item 3: the lock file is runtime state, never removed, so a second
    holder that flocks the same inode after a refusal still excludes the next run."""
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    beads.beads["v1"].metadata["verdict"] = "inconclusive"
    with pytest.raises(MergeError):
        _run(beads, hub)
    lock_path = hub / ".helios" / "runs" / "b1" / "lock"
    assert lock_path.exists()
    with lock_path.open("r+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        beads.beads["v1"].metadata["verdict"] = "verified"
        with pytest.raises(MergeError) as error:
            _run(beads, hub)
        assert error.value.code == 2
        assert str(error.value) == "merge lock is held"


def test_dry_run_creates_no_lock_directory(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    assert _run(beads, hub, dry_run=True)[0] == 0
    assert not (hub / ".helios" / "runs" / "b1").exists()


def test_dirty_beads_export_in_hub_still_merges(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    (hub / ".beads" / "issues.jsonl").write_text('{"dirtied": true}\n')
    code, message = _run(beads, hub)
    assert (code, message) == (0, "merged")


def test_clean_check_ignores_beads_export_in_hub_only(tmp_path: Path) -> None:
    """SPEC 12 item 7: `.beads/` dirt is ignored in the hub only; in the worktree it
    is dirt like any other path."""
    hub, worktree = _repo(tmp_path)
    import helios.merge as merge_module

    assert merge_module._clean(hub, ignore_beads=True) is True
    assert merge_module._clean(worktree, ignore_beads=False) is True
    (hub / ".beads" / "issues.jsonl").write_text('{"dirtied": true}\n')
    (worktree / ".beads" / "issues.jsonl").write_text('{"dirtied": true}\n')
    assert merge_module._clean(hub, ignore_beads=True) is True
    assert merge_module._clean(worktree, ignore_beads=False) is False


def test_worktree_beads_dirt_refuses_merge(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    (worktree / ".beads" / "issues.jsonl").write_text('{"dirtied": true}\n')
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value) == "hub or worktree is dirty"


def test_branch_changing_beads_refuses_merge(tmp_path: Path) -> None:
    """SPEC 12 item 7: the branch itself must not touch `.beads/`, even committed."""
    hub, worktree = _repo(tmp_path)
    (worktree / ".beads" / "issues.jsonl").write_text('{"branch": true}\n')
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "touch beads")
    beads = _beads(hub, worktree)
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value) == "branch changes .beads/"


def test_clean_parser_treats_rename_into_beads_as_dirty(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    import helios.merge as merge_module

    _git(hub, "mv", "value.txt", ".beads/value.txt")
    assert merge_module._clean(hub, ignore_beads=True) is False


def test_clean_parser_arrow_named_directory_is_not_confused_with_beads(tmp_path: Path) -> None:
    """SPEC 12 item 6: `-z` parsing must not fall for a directory literally named
    `x -> .beads`, the way a naive `" -> "` string split would."""
    hub, worktree = _repo(tmp_path)
    import helios.merge as merge_module

    d = hub / "x -> .beads"
    d.mkdir()
    (d / "y").write_text("1\n")
    _git(hub, "add", "-A")
    _git(hub, "commit", "-m", "arrow dir")
    (d / "y").write_text("2\n")
    assert merge_module._clean(hub, ignore_beads=True) is False


def test_missing_worktree_without_recovery_is_step_two_refusal(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    before = _git(hub, "rev-parse", "main")
    shutil.rmtree(worktree)
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value) == "worktree missing for b1"
    assert beads.argv_log == []
    assert "merge_main_before" not in beads.beads["b1"].metadata
    assert _git(hub, "rev-parse", "main") == before


def test_happy_path_records_exact_markers_and_runs_checks(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    main_before = _git(hub, "rev-parse", "main")
    calls: list[tuple[str, Path]] = []
    code, _ = _run(beads, hub, runner=lambda command, cwd: calls.append((command, cwd)) or 0)
    assert code == 0
    assert calls == [("uv run pytest -q", worktree)]
    texts = [comment.text for comment in beads.comments("b1")]
    assert texts == [
        f"merge: [b1@{main_before}:rebased] rebased",
        f"merge: [b1@{main_before}:tested] tested",
        f"merge: [b1@{main_before}:merged] merged",
        f"merge: [b1@{main_before}:removed] removed",
    ]
    assert beads.beads["b1"].metadata["merge_main_before"] == main_before


def test_merge_commit_metadata_precedes_ff_merge(tmp_path: Path, monkeypatch) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    import helios.merge as merge_module

    original = merge_module._git
    observed: list[str] = []

    def record(cwd: Path, *args: str):
        if cwd == hub and args == ("merge", "--ff-only", "worktree-b1"):
            observed.append(str(beads.beads["b1"].metadata.get("merge_commit")))
        return original(cwd, *args)

    monkeypatch.setattr(merge_module, "_git", record)
    _run(beads, hub)
    assert observed == [beads.beads["b1"].metadata["merge_commit"]]


def test_push_happens_only_when_origin_exists(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    _run(beads, hub)
    assert "pushed" not in " ".join(comment.text for comment in beads.comments("b1"))
    hub2, worktree2 = _repo(tmp_path / "with-origin")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(hub2, "remote", "add", "origin", str(origin))
    beads2 = _beads(hub2, worktree2)
    _run(beads2, hub2)
    assert any(comment.text.endswith("pushed") for comment in beads2.comments("b1"))


def test_rebase_conflict_aborts_and_sets_conflict_state(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    (hub / "value.txt").write_text("hub\n")
    _git(hub, "add", ".")
    _git(hub, "commit", "-m", "conflict")
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 3
    assert beads.states["b1"]["run"] == "conflict"
    assert not (worktree / ".git" / "rebase-merge").exists()


def test_main_moved_during_checks_is_exit_three(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)

    def move_main(_command: str, _cwd: Path) -> int:
        (hub / "main.txt").write_text("moved\n")
        _git(hub, "add", "main.txt")
        _git(hub, "commit", "-m", "move")
        return 0

    with pytest.raises(MergeError) as error:
        _run(beads, hub, runner=move_main)
    assert error.value.code == 3
    assert any(comment.text.endswith("main-moved") for comment in beads.comments("b1"))


def test_ff_only_failure_when_main_moved_is_exit_three(tmp_path: Path, monkeypatch) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    import helios.merge as merge_module

    original = merge_module._git
    raced = False

    def race(cwd: Path, *args: str):
        nonlocal raced
        if not raced and cwd == hub and args == ("merge", "--ff-only", "worktree-b1"):
            raced = True
            (hub / "race.txt").write_text("race\n")
            original(hub, "add", "race.txt")
            original(hub, "commit", "-m", "race")
        return original(cwd, *args)

    monkeypatch.setattr(merge_module, "_git", race)
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 3
    assert any(comment.text.endswith("main-moved") for comment in beads.comments("b1"))
    assert beads.beads["b1"].metadata["merge_commit"] == ""
    monkeypatch.setattr(merge_module, "_git", original)
    assert _run(beads, hub) == (0, "merged")


def test_check_failure_is_exit_five(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    with pytest.raises(MergeError) as error:
        _run(beads, hub, runner=lambda _command, _cwd: 1)
    assert error.value.code == 5
    assert any(comment.text.endswith("test-failed") for comment in beads.comments("b1"))


def test_push_failure_is_exit_four(tmp_path: Path, monkeypatch) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    _git(tmp_path, "init", "--bare", str(tmp_path / "origin.git"))
    _git(hub, "remote", "add", "origin", str(tmp_path / "origin.git"))
    import helios.merge as merge_module

    original = merge_module._git

    def fail_push(cwd: Path, *args: str):
        if args == ("push", "origin", "main"):
            return subprocess.CompletedProcess(["git", *args], 1, "", "push failed")
        return original(cwd, *args)

    monkeypatch.setattr(merge_module, "_git", fail_push)
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 4


def test_removal_failure_is_exit_four(tmp_path: Path, monkeypatch) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    import helios.merge as merge_module

    monkeypatch.setattr(
        merge_module,
        "_remove_worktree",
        lambda *_args: (_ for _ in ()).throw(MergeError("removal failed", 4)),
    )
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 4


def test_stale_worktree_registration_is_pruned_before_branch_removal(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    main_before = _git(hub, "rev-parse", "main")
    _git(hub, "merge", "--ff-only", "worktree-b1")
    merge_commit = _git(hub, "rev-parse", "main")
    beads.beads["b1"].metadata["merge_commit"] = merge_commit
    beads.beads["b1"].metadata["merge_main_before"] = main_before
    # Simulate a partial removal: the directory is gone but git's own worktree
    # registration under .git/worktrees/ was never cleaned up.
    shutil.rmtree(worktree)
    assert _run(beads, hub) == (0, "recovered")
    assert _git(hub, "branch", "--list", "worktree-b1") == ""
    assert "worktree-b1" not in _git(hub, "worktree", "list", "--porcelain")


def test_recovery_writes_merged_marker_when_missing_after_crash_before_comment(
    tmp_path: Path, monkeypatch
) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    import helios.merge as merge_module

    original = merge_module._git

    def crash_right_after_ff(cwd: Path, *args: str):
        result = original(cwd, *args)
        if cwd == hub and args == ("merge", "--ff-only", "worktree-b1") and result.returncode == 0:
            raise RuntimeError("crash")
        return result

    monkeypatch.setattr(merge_module, "_git", crash_right_after_ff)
    with pytest.raises(RuntimeError):
        _run(beads, hub)
    monkeypatch.setattr(merge_module, "_git", original)
    assert beads.beads["b1"].metadata["merge_commit"]
    assert not any(c.text.endswith("merged") for c in beads.comments("b1"))
    assert _run(beads, hub) == (0, "recovered")
    assert not worktree.exists()
    assert len([c for c in beads.comments("b1") if c.text.endswith("merged")]) == 1


def test_recovery_after_merge_and_after_removal_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    original = __import__("helios.merge", fromlist=["_comment"])._comment

    def crash_after_merge(store, bead, marker, detail):
        original(store, bead, marker, detail)
        if detail == "merged":
            raise RuntimeError("crash")

    monkeypatch.setattr("helios.merge._comment", crash_after_merge)
    with pytest.raises(RuntimeError):
        _run(beads, hub)
    monkeypatch.setattr("helios.merge._comment", original)
    assert _run(beads, hub) == (0, "recovered")
    assert not worktree.exists()
    assert len([c for c in beads.comments("b1") if c.text.endswith("merged")]) == 1


def test_recovery_after_removal_skips_gone_targets(tmp_path: Path, monkeypatch) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    import helios.merge as merge_module

    original = merge_module._comment

    def crash_after_removal(store, bead, marker, detail):
        original(store, bead, marker, detail)
        if detail == "removed":
            raise RuntimeError("crash")

    monkeypatch.setattr(merge_module, "_comment", crash_after_removal)
    with pytest.raises(RuntimeError):
        _run(beads, hub)
    monkeypatch.setattr(merge_module, "_comment", original)
    assert not worktree.exists()
    assert _run(beads, hub) == (0, "recovered")
    assert len([c for c in beads.comments("b1") if c.text.endswith("removed")]) == 1


@pytest.mark.parametrize("crash_detail", ["rebased", "tested", "merged"])
def test_replay_after_each_merge_step_has_one_marker(
    tmp_path: Path, monkeypatch, crash_detail: str
) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    import helios.merge as merge_module

    original = merge_module._comment

    def crash(store, bead, marker, detail):
        original(store, bead, marker, detail)
        if detail == crash_detail:
            raise RuntimeError("crash")

    monkeypatch.setattr(merge_module, "_comment", crash)
    with pytest.raises(RuntimeError):
        _run(beads, hub)
    monkeypatch.setattr(merge_module, "_comment", original)
    _run(beads, hub)
    texts = [comment.text for comment in beads.comments("b1")]
    assert len(texts) == len(set(texts))


def test_replay_after_push_has_one_marker(tmp_path: Path, monkeypatch) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(hub, "remote", "add", "origin", str(origin))
    import helios.merge as merge_module

    original = merge_module._comment

    def crash(store, bead, marker, detail):
        original(store, bead, marker, detail)
        if detail == "pushed":
            raise RuntimeError("crash")

    monkeypatch.setattr(merge_module, "_comment", crash)
    with pytest.raises(RuntimeError):
        _run(beads, hub)
    monkeypatch.setattr(merge_module, "_comment", original)
    _run(beads, hub)
    assert len([c for c in beads.comments("b1") if c.text.endswith("pushed")]) == 1


def test_new_main_before_uses_new_marker_set(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    first_main = _git(hub, "rev-parse", "main")
    def move_main(_command: str, _cwd: Path) -> int:
        (hub / "between.txt").write_text("main moved\n")
        _git(hub, "add", "between.txt")
        _git(hub, "commit", "-m", "between merges")
        return 0

    with pytest.raises(MergeError):
        _run(beads, hub, runner=move_main)
    second_main = _git(hub, "rev-parse", "main")
    _run(beads, hub)
    texts = [comment.text for comment in beads.comments("b1")]
    assert any(f"[b1@{first_main}:main-moved]" in text for text in texts)
    assert any(f"[b1@{second_main}:merged]" in text for text in texts)


def test_dry_run_preserves_refs_worktrees_and_bead_log(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    refs = _git(hub, "show-ref")
    trees = _git(hub, "worktree", "list", "--porcelain")
    log = list(beads.argv_log)
    assert _run(beads, hub, dry_run=True)[0] == 0
    assert _git(hub, "show-ref") == refs
    assert _git(hub, "worktree", "list", "--porcelain") == trees
    assert beads.argv_log == log


def test_command_writes_refusal_to_stderr_and_planned_steps_to_stdout(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree, real_hashes=True)
    beads.beads["v1"].metadata["verdict"] = "inconclusive"
    import helios.commands.merge as command
    from helios.config import Config

    monkeypatch.setattr(command, "load", lambda _cwd: Config(hub=hub, project=_project()))
    monkeypatch.setattr(command, "Beads", lambda _hub: beads)
    assert command.run(Namespace(bead="b1", dry_run=False)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "helios: verify evidence is not closed and verified\n"
    beads.beads["v1"].metadata["verdict"] = "verified"
    assert command.run(Namespace(bead="b1", dry_run=True)) == 0
    assert capsys.readouterr().out == "would rebase, test, merge, push, and remove\n"


@pytest.mark.parametrize(
    "toml",
    ["[project]\nbogus = 1\n", "[project]\ntest = 3\n"],
    ids=["unknown-key", "wrong-type"],
)
def test_bad_workflow_toml_is_exit_two_not_a_traceback(
    tmp_path: Path, monkeypatch, capsys, toml: str
) -> None:
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "workflow.toml").write_text(toml)
    import helios.commands.merge as command

    monkeypatch.chdir(tmp_path)
    assert command.run(Namespace(bead="b1", dry_run=False)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("helios: ")


# ---------------------------------------------------------------- item 1: step 2
# precedes step 8, so recovery never destroys a dirty worktree.


def test_dirty_worktree_refuses_before_recovery_runs(tmp_path: Path, monkeypatch) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    import helios.merge as merge_module

    original = merge_module._git

    def crash_after_ff(cwd: Path, *args: str):
        result = original(cwd, *args)
        if cwd == hub and args == ("merge", "--ff-only", "worktree-b1") and result.returncode == 0:
            raise RuntimeError("crash")
        return result

    monkeypatch.setattr(merge_module, "_git", crash_after_ff)
    with pytest.raises(RuntimeError):
        _run(beads, hub)
    monkeypatch.setattr(merge_module, "_git", original)
    (worktree / "value.txt").write_text("uncommitted user edit\n")
    with pytest.raises(MergeError) as dry_error:
        _run(beads, hub, dry_run=True)
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert dry_error.value.code == 2
    assert error.value.code == 2
    assert str(error.value) == "hub or worktree is dirty"
    assert worktree.exists()


# Item 2 (unlock a missing, locked worktree registration before pruning and
# deleting the branch) is covered by test_stale_worktree_registration_is_pruned_
# before_branch_removal above, now that `_repo` locks the worktree per SPEC 7.3.

# ---------------------------------------------------------------- item 4: output
# streams (checks captured, failure detail, no bead, generic exceptions).


def test_check_failure_reports_last_lines_of_captured_output(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    project = ProjectConfig(worktrees="../worktree", test="echo OUT; echo ERR 1>&2; exit 7")
    with pytest.raises(MergeError) as error:
        merge_bead(hub, "b1", project=project, beads=beads, input_hashes=lambda _b: {})
    assert error.value.code == 5
    assert str(error.value) == "test failed with exit 7\nOUT\nERR"


def test_command_discards_successful_check_output(tmp_path: Path, monkeypatch, capsys) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree, real_hashes=True)
    project = ProjectConfig(worktrees="../worktree", test="echo NOISY-OUT; echo NOISY-ERR 1>&2")
    import helios.commands.merge as command
    from helios.config import Config

    monkeypatch.setattr(command, "load", lambda _cwd: Config(hub=hub, project=project))
    monkeypatch.setattr(command, "Beads", lambda _hub: beads)
    assert command.run(Namespace(bead="b1", dry_run=False)) == 0
    captured = capsys.readouterr()
    assert captured.out == "merged\n"
    assert captured.err == ""


def test_command_prints_prefixed_failure_output_from_noisy_check(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree, real_hashes=True)
    project = ProjectConfig(
        worktrees="../worktree", test="echo NOISY-OUT; echo NOISY-ERR 1>&2; exit 3"
    )
    import helios.commands.merge as command
    from helios.config import Config

    monkeypatch.setattr(command, "load", lambda _cwd: Config(hub=hub, project=project))
    monkeypatch.setattr(command, "Beads", lambda _hub: beads)
    assert command.run(Namespace(bead="b1", dry_run=False)) == 5
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "helios: test failed with exit 3\nhelios: NOISY-OUT\nhelios: NOISY-ERR\n"


def test_command_unknown_bead_prints_no_bead_and_exits_two(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    import helios.commands.merge as command
    from helios.config import Config

    monkeypatch.setattr(command, "load", lambda _cwd: Config(hub=hub, project=_project()))
    monkeypatch.setattr(command, "Beads", lambda _hub: beads)
    assert command.run(Namespace(bead="nope", dry_run=False)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "helios: no bead nope\n"


def test_command_generic_exception_prints_message_and_exits_one(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    hub, worktree = _repo(tmp_path)

    class Boom:
        def show(self, _bead_id: str):
            raise OSError("disk exploded")

    import helios.commands.merge as command
    from helios.config import Config

    monkeypatch.setattr(command, "load", lambda _cwd: Config(hub=hub, project=_project()))
    monkeypatch.setattr(command, "Beads", lambda _hub: Boom())
    assert command.run(Namespace(bead="b1", dry_run=False)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "helios: disk exploded\n"


def test_command_unreadable_workflow_toml_exits_one(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / ".agents").mkdir()
    toml_path = tmp_path / ".agents" / "workflow.toml"
    toml_path.write_text("[project]\n")
    toml_path.chmod(0)
    import helios.commands.merge as command

    monkeypatch.chdir(tmp_path)
    try:
        assert command.run(Namespace(bead="b1", dry_run=False)) == 1
    finally:
        toml_path.chmod(0o644)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("helios: ")


def test_push_rejection_stderr_is_fully_prefixed(tmp_path: Path, monkeypatch, capsys) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree, real_hashes=True)
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(hub, "remote", "add", "origin", str(origin))
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'line one' >&2\necho 'line two' >&2\nexit 1\n")
    hook.chmod(0o755)
    import helios.commands.merge as command
    from helios.config import Config

    project = ProjectConfig(worktrees="../worktree", test="true", typecheck="")
    monkeypatch.setattr(command, "load", lambda _cwd: Config(hub=hub, project=project))
    monkeypatch.setattr(command, "Beads", lambda _hub: beads)
    assert command.run(Namespace(bead="b1", dry_run=False)) == 4
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""
    assert all(line.startswith("helios: ") for line in captured.err.splitlines())


# ---------------------------------------------------------------- item 5: a rebase
# failure that is not a conflict.


def test_rebase_failure_without_conflict_is_exit_four(tmp_path: Path, monkeypatch) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    import helios.merge as merge_module

    monkeypatch.setattr(merge_module, "_clean", lambda _path, ignore_beads=True: True)
    (worktree / "value.txt").write_text("unstaged local edit\n")
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 4
    assert str(error.value).startswith("rebase failed:")
    rebase_merge = _git(worktree, "rev-parse", "--git-path", "rebase-merge")
    rebase_apply = _git(worktree, "rev-parse", "--git-path", "rebase-apply")
    assert not (worktree / rebase_merge).exists()
    assert not (worktree / rebase_apply).exists()


# ---------------------------------------------------------------- item 8: only a
# verified commit merges.


def test_worktree_on_wrong_branch_refuses_merge(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    _git(worktree, "checkout", "-b", "rogue")
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value) == "worktree is not on branch worktree-b1"


def test_worktree_head_ahead_of_output_commit_refuses_merge(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    (worktree / "extra.txt").write_text("unverified\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "after verification")
    new_head = _git(worktree, "rev-parse", "HEAD")
    old_commit = beads.beads["b1"].metadata["output_commit"]
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value) == f"worktree HEAD {new_head} is not the verified output_commit {old_commit}"


# ---------------------------------------------------------------- item 9: a second
# merge with a new verified commit is a fresh merge, not a recovery.


def test_second_merge_with_new_verified_commit_creates_new_markers(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    assert _run(beads, hub) == (0, "merged")
    first_main_before = beads.beads["b1"].metadata["merge_main_before"]

    _git(hub, "worktree", "add", str(worktree), "-b", "worktree-b1", "main")
    (worktree / "second.txt").write_text("second\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "second change")
    new_head = _git(worktree, "rev-parse", "HEAD")
    beads.beads["b1"].metadata["output_commit"] = new_head
    _new_envelope(hub, worktree)

    assert _run(beads, hub) == (0, "merged")
    assert _git(hub, "show", "main:second.txt") == "second"
    assert _git(hub, "show", "main:value.txt") == "merged"
    second_main_before = beads.beads["b1"].metadata["merge_main_before"]
    second_merge_commit = beads.beads["b1"].metadata["merge_commit"]
    assert second_main_before != first_main_before
    assert second_merge_commit == new_head
    texts = [comment.text for comment in beads.comments("b1")]
    assert texts.count(f"merge: [b1@{first_main_before}:merged] merged") == 1
    assert texts.count(f"merge: [b1@{second_main_before}:merged] merged") == 1


# ---------------------------------------------------------------- h3c-fix4 item 1:
# the verified-commit check runs on every attempt; a rebase is accepted by patch-id.


def _crash_right_after_rebase(monkeypatch):
    import helios.merge as merge_module

    original = merge_module._git

    def crash(cwd: Path, *args: str):
        result = original(cwd, *args)
        if args == ("rebase", "main") and result.returncode == 0:
            raise RuntimeError("crash")
        return result

    monkeypatch.setattr(merge_module, "_git", crash)
    return original


def test_hand_fix_commit_after_check_failure_refuses(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    with pytest.raises(MergeError) as error:
        _run(beads, hub, runner=lambda _c, _w: 1)
    assert error.value.code == 5
    (worktree / "handfix.txt").write_text("fix\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "hand fix")
    with pytest.raises(MergeError) as error2:
        _run(beads, hub)
    assert error2.value.code == 2
    assert str(error2.value).startswith("worktree HEAD")


def test_hand_resolved_conflict_with_different_patch_refuses(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    (hub / "value.txt").write_text("hub\n")
    _git(hub, "add", ".")
    _git(hub, "commit", "-m", "conflict")
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 3
    proc = subprocess.run(["git", "rebase", "main"], cwd=worktree, capture_output=True, text=True)
    assert proc.returncode != 0
    (worktree / "value.txt").write_text("resolved-differently\n")
    _git(worktree, "add", "value.txt")
    _git(worktree, "-c", "core.editor=true", "rebase", "--continue")
    with pytest.raises(MergeError) as error2:
        _run(beads, hub)
    assert error2.value.code == 2
    assert str(error2.value).startswith("worktree HEAD")


@pytest.mark.parametrize("mutation", ["extra-commit", "amend", "beads-commit"])
def test_worktree_mutation_after_post_rebase_crash_refuses(
    tmp_path: Path, monkeypatch, mutation: str
) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    import helios.merge as merge_module

    original = _crash_right_after_rebase(monkeypatch)
    with pytest.raises(RuntimeError):
        _run(beads, hub)
    monkeypatch.setattr(merge_module, "_git", original)
    if mutation == "extra-commit":
        (worktree / "extra.txt").write_text("extra\n")
        _git(worktree, "add", ".")
        _git(worktree, "commit", "-m", "extra")
    elif mutation == "amend":
        (worktree / "value.txt").write_text("amended\n")
        _git(worktree, "add", ".")
        _git(worktree, "commit", "--amend", "--no-edit")
    else:
        (worktree / ".beads" / "extra.jsonl").write_text("{}\n")
        _git(worktree, "add", ".")
        _git(worktree, "commit", "-m", "beads change")
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2


def test_post_rebase_crash_then_main_moves_then_rerun_merges(tmp_path: Path, monkeypatch) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    # Move main once before the first attempt, so its rebase is a real rebase (a
    # new commit hash), not a no-op.
    (hub / "first.txt").write_text("first\n")
    _git(hub, "add", "first.txt")
    _git(hub, "commit", "-m", "first move")
    import helios.merge as merge_module

    original = _crash_right_after_rebase(monkeypatch)
    with pytest.raises(RuntimeError):
        _run(beads, hub)
    monkeypatch.setattr(merge_module, "_git", original)
    rebased_once = _git(worktree, "rev-parse", "HEAD")
    assert rebased_once != beads.beads["b1"].metadata["output_commit"]
    # Move main again; the rerun's own rebase must run a second time.
    (hub / "second.txt").write_text("second\n")
    _git(hub, "add", "second.txt")
    _git(hub, "commit", "-m", "second move")
    final_main_before = _git(hub, "rev-parse", "main")
    assert _run(beads, hub) == (0, "merged")
    texts = [comment.text for comment in beads.comments("b1")]
    for step in ("rebased", "tested", "merged", "removed"):
        assert texts.count(f"merge: [b1@{final_main_before}:{step}] {step}") == 1


def test_second_merge_crash_after_rebase_then_rerun_merges(tmp_path: Path, monkeypatch) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    assert _run(beads, hub) == (0, "merged")
    _git(hub, "worktree", "add", str(worktree), "-b", "worktree-b1", "main")
    (worktree / "second.txt").write_text("second\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "second change")
    new_head = _git(worktree, "rev-parse", "HEAD")
    beads.beads["b1"].metadata["output_commit"] = new_head
    _new_envelope(hub, worktree)
    import helios.merge as merge_module

    original = _crash_right_after_rebase(monkeypatch)
    with pytest.raises(RuntimeError):
        _run(beads, hub)
    monkeypatch.setattr(merge_module, "_git", original)
    assert _run(beads, hub) == (0, "merged")
    assert _git(hub, "show", "main:second.txt") == "second"


# ---------------------------------------------------------------- h3c-fix4 items
# 2, 3, 4.


def test_check_success_with_non_utf8_output_does_not_crash(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    project = ProjectConfig(worktrees="../worktree", test="printf '\\xff'")
    code, message = merge_bead(hub, "b1", project=project, beads=beads, input_hashes=lambda _b: {})
    assert (code, message) == (0, "merged")


def test_branch_renaming_out_of_beads_still_refuses(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    _git(worktree, "mv", ".beads/issues.jsonl", "notbeads.txt")
    _git(worktree, "commit", "-m", "rename out of beads")
    beads = _beads(hub, worktree)
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value) == "branch changes .beads/"


def test_clean_does_not_ignore_a_change_to_a_path_named_dot_beads_itself(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    import helios.merge as merge_module

    shutil.rmtree(hub / ".beads")
    (hub / ".beads").write_text("not a directory\n")
    _git(hub, "add", "-A")
    assert merge_module._clean(hub, ignore_beads=True) is False


# ---------------------------------------------------------------- h3c-fix5 item 1:
# patch-id is computed from bytes (--verbatim), never --stable, so a
# whitespace-only edit is a different, unverified commit.


def _repo_for_patch_id(tmp_path: Path, content: str) -> tuple[Path, Path]:
    hub = tmp_path / "repo"
    hub.mkdir(parents=True)
    _git(hub, "init", "-b", "main")
    _git(hub, "config", "user.email", "test@example.com")
    _git(hub, "config", "user.name", "Test")
    (hub / "value.txt").write_text("base\n")
    (hub / ".beads").mkdir()
    (hub / ".beads" / "issues.jsonl").write_text("{}\n")
    _git(hub, "add", ".")
    _git(hub, "commit", "-m", "base")
    worktree = tmp_path / "worktree" / "b1"
    worktree.parent.mkdir()
    _git(hub, "worktree", "add", "-b", "worktree-b1", str(worktree), "main")
    (worktree / "value.txt").write_text(content)
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "change")
    return hub, worktree


WHITESPACE_VARIANTS = {
    "indent": ("def f():\n    return 1\n", "def f():\n        return 1\n"),
    "inside-string": ('x = "a b"\n', 'x = "a  b"\n'),
    "makefile-tab": ("target:\n\tcommand\n", "target:\n    command\n"),
    "trailing-space": ("line\n", "line \n"),
    "crlf": ("line\n", "line\r\n"),
}


@pytest.mark.parametrize("variant", list(WHITESPACE_VARIANTS))
def test_whitespace_only_edit_refuses_fresh_and_after_check_failure(tmp_path: Path, variant: str) -> None:
    original, edited = WHITESPACE_VARIANTS[variant]

    hub, worktree = _repo_for_patch_id(tmp_path / "fresh", original)
    beads = _beads(hub, worktree)
    (worktree / "value.txt").write_text(edited)
    _git(worktree, "add", ".")
    _git(worktree, "commit", "--amend", "--no-edit")
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value).startswith("worktree HEAD")

    hub2, worktree2 = _repo_for_patch_id(tmp_path / "after-exit5", original)
    beads2 = _beads(hub2, worktree2)
    with pytest.raises(MergeError) as check_error:
        _run(beads2, hub2, runner=lambda _c, _w: 1)
    assert check_error.value.code == 5
    (worktree2 / "value.txt").write_text(edited)
    _git(worktree2, "add", ".")
    _git(worktree2, "commit", "--amend", "--no-edit")
    with pytest.raises(MergeError) as error2:
        _run(beads2, hub2)
    assert error2.value.code == 2
    assert str(error2.value).startswith("worktree HEAD")


def test_identical_recommit_still_merges(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    _git(worktree, "commit", "--amend", "--no-edit")
    assert _run(beads, hub) == (0, "merged")


def test_hand_rebase_onto_newer_main_still_merges(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    (hub / "other.txt").write_text("other\n")
    _git(hub, "add", "other.txt")
    _git(hub, "commit", "-m", "advance main")
    _git(worktree, "rebase", "main")
    assert _run(beads, hub) == (0, "merged")


# ---------------------------------------------------------------- h3c-fix5 item 2:
# no `_git` call raises UnicodeDecodeError.


def test_non_utf8_file_in_verified_commit_merges_after_check_failure_and_main_ahead(
    tmp_path: Path,
) -> None:
    hub, worktree = _repo(tmp_path)
    (worktree / "binary.dat").write_bytes(b"caf\xe9")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "binary content")
    beads = _beads(hub, worktree)
    with pytest.raises(MergeError) as error:
        _run(beads, hub, runner=lambda _c, _w: 1)
    assert error.value.code == 5
    (hub / "ahead.txt").write_text("ahead\n")
    _git(hub, "add", "ahead.txt")
    _git(hub, "commit", "-m", "main ahead")
    assert _run(beads, hub) == (0, "merged")


def test_non_utf8_filename_does_not_crash_clean_check_or_beads_diff(tmp_path: Path, monkeypatch) -> None:
    """This host's filesystem (APFS) rejects a non-UTF-8 name outright, so this
    feeds `_clean` and the branch-diff path list the surrogate-escaped text a real
    non-UTF-8 filename decodes to (via `_git`'s own `errors="surrogateescape"`),
    and checks neither raises."""
    import helios.merge as merge_module

    odd_name = b"weird-\xe9.txt".decode("utf-8", "surrogateescape")

    def fake_status(_cwd: Path, *args: str):
        return subprocess.CompletedProcess(["git", *args], 0, f" M {odd_name}\0", "")

    monkeypatch.setattr(merge_module, "_git", fake_status)
    assert merge_module._clean(tmp_path, ignore_beads=False) is False

    def fake_diff(_cwd: Path, *args: str):
        return subprocess.CompletedProcess(["git", *args], 0, f"{odd_name}\n", "")

    monkeypatch.setattr(merge_module, "_git", fake_diff)
    assert merge_module._git_output(tmp_path, "diff").splitlines() == [odd_name]


# ---------------------------------------------------------------- h3c-fix5 item 3:
# an empty verified range never passes the patch-id comparison.


def test_empty_verified_range_refuses(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    assert _run(beads, hub) == (0, "merged")
    first_merge_commit = beads.beads["b1"].metadata["merge_commit"]

    _git(hub, "worktree", "add", str(worktree), "-b", "worktree-b1", "main")
    (worktree / "second.txt").write_text("second\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "second change")
    new_head = _git(worktree, "rev-parse", "HEAD")

    # A stale output_commit that is already on main, not the branch's actual
    # (unverified) tip: merge-base(output_commit, main)..output_commit is empty.
    beads.beads["b1"].metadata["output_commit"] = first_merge_commit
    envelope_path = hub / ".helios" / "runs" / "v1" / "attempt-1" / "envelope.json"
    payload = Envelope.model_validate_json(envelope_path.read_text()).model_copy(
        update={"base_commit": first_merge_commit}
    )
    envelope_path.write_text(payload.model_dump_json())

    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value) == f"worktree HEAD {new_head} is not the verified output_commit {first_merge_commit}"


# ---------------------------------------------------------------- h3c-fix5 item 4:
# a missing output_commit object refuses before any merge-base call.


def test_missing_output_commit_object_refuses(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    missing = "0" * 40
    beads.beads["b1"].metadata["output_commit"] = missing
    envelope_path = hub / ".helios" / "runs" / "v1" / "attempt-1" / "envelope.json"
    payload = Envelope.model_validate_json(envelope_path.read_text()).model_copy(update={"base_commit": missing})
    envelope_path.write_text(payload.model_dump_json())
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value) == f"verified output_commit {missing} is not a commit"


BD = shutil.which("bd")


@pytest.mark.skipif(BD is None, reason="bd is not installed")
def test_real_bd_closed_set_state_does_not_reopen(tmp_path: Path) -> None:
    assert BD is not None
    _git(tmp_path, "init")
    subprocess.run([BD, "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    bead = subprocess.run(
        [BD, "create", "closed", "--silent"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run([BD, "close", bead], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run([BD, "set-state", bead, "run=conflict"], cwd=tmp_path, check=True, capture_output=True, text=True)
    payload = subprocess.run(
        [BD, "show", bead, "--json"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout
    assert '"status":"closed"' in payload.replace(" ", "")


@pytest.mark.skipif(BD is None, reason="bd is not installed")
def test_real_bd_hand_fix_after_check_failure_refuses(tmp_path: Path) -> None:
    """h3c-fix4 item 1's exit-5-then-hand-fix case, against real bd."""
    assert BD is not None
    hub = tmp_path / "hub"
    hub.mkdir()
    _git(hub, "init", "-b", "main")
    _git(hub, "config", "user.email", "test@example.com")
    _git(hub, "config", "user.name", "Test")
    (hub / "value.txt").write_text("base\n")
    _git(hub, "add", ".")
    _git(hub, "commit", "-m", "base")
    subprocess.run(
        [BD, "init", "--non-interactive", "--prefix", "t", "--skip-agents", "--quiet"],
        cwd=hub,
        check=True,
        capture_output=True,
        text=True,
    )
    beads = Beads(hub)
    impl = beads.create("impl", labels=["unit:u1"], metadata={"unit": "u1", "kind": "impl"})
    worktree = hub / ".claude" / "worktrees" / impl
    worktree.parent.mkdir(parents=True)
    _git(hub, "worktree", "add", str(worktree), "-b", f"worktree-{impl}", "main")
    (worktree / "feature.txt").write_text("feature\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "feature")
    output_commit = _git(worktree, "rev-parse", "HEAD")

    beads.set_metadata(impl, {"output_commit": output_commit})
    beads.close(impl, "done")
    verify = beads.create(
        "verify", labels=["unit:u1"], metadata={"unit": "u1", "kind": "verify-code", "parent": impl}
    )
    beads.set_metadata(verify, {"verdict": "verified", "attempt": "1"})
    beads.close(verify, "verified")
    envelope_dir = hub / ".helios" / "runs" / verify / "attempt-1"
    envelope_dir.mkdir(parents=True)
    (envelope_dir / "envelope.json").write_text(
        Envelope(
            task_id=verify,
            attempt=1,
            attempt_id=f"{verify}#1",
            kind="verify-code",
            harness="fake",
            started_at="now",
            base_commit=output_commit,
            input_hashes={},
            execution_status=ExecutionStatus.COMPLETED,
            report=AgentReport(status=WorkStatus.DONE, summary="ok"),
        ).model_dump_json()
    )

    project = ProjectConfig(worktrees=".claude/worktrees")
    with pytest.raises(MergeError) as error:
        merge_bead(
            hub,
            impl,
            project=project,
            beads=beads,
            check_runner=lambda _c, _w: 1,
            input_hashes=lambda _b: {},
        )
    assert error.value.code == 5

    (worktree / "handfix.txt").write_text("fix\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "hand fix")

    with pytest.raises(MergeError) as error2:
        merge_bead(hub, impl, project=project, beads=beads, input_hashes=lambda _b: {})
    assert error2.value.code == 2
    assert str(error2.value).startswith("worktree HEAD")


# ---------------------------------------------------------------- h3c-fix6 item 1,
# superseded by h3c-fix7 item 1: the verified-commit check replays the verified
# range with `git merge-tree` and compares trees, never a diff or a fingerprint.
# Ambient diff config cannot hide a change because no diff is taken at all, and
# the mode/NUL/rename quirks that fooled patch-id are just part of the tree.


def _apply_ops(cwd: Path, ops: list[tuple]) -> None:
    for kind, path, value in ops:
        if kind == "write":
            target = cwd / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
            _git(cwd, "add", "--", path)
        elif kind == "chmod":
            (cwd / path).chmod(value)
            _git(cwd, "add", "--", path)
        elif kind == "mv":
            _git(cwd, "mv", path, value)
        elif kind == "gitlink":
            (cwd / path).mkdir(exist_ok=True)  # an empty dir is an unpopulated submodule, not dirt
            _git(cwd, "update-index", "--add", "--cacheinfo", f"160000,{value},{path}")
        else:
            raise AssertionError(kind)


def _fp_repo(tmp_path: Path, base_ops: list[tuple], repo_cfg: dict[str, str] | None = None) -> tuple[Path, Path]:
    hub = tmp_path / "repo"
    hub.mkdir(parents=True)
    _git(hub, "init", "-b", "main")
    _git(hub, "config", "user.email", "test@example.com")
    _git(hub, "config", "user.name", "Test")
    (hub / ".beads").mkdir()
    (hub / ".beads" / "issues.jsonl").write_text("{}\n")
    _git(hub, "add", ".beads")
    _apply_ops(hub, base_ops)
    _git(hub, "commit", "-m", "base")
    for key, value in (repo_cfg or {}).items():
        _git(hub, "config", key, value)
    worktree = tmp_path / "worktree" / "b1"
    worktree.parent.mkdir()
    _git(hub, "worktree", "add", "-b", "worktree-b1", str(worktree), "main")
    return hub, worktree


def _fp_case_refuses(
    tmp_path: Path,
    base_ops: list[tuple],
    verified_ops: list[tuple],
    tamper_ops: list[tuple],
    *,
    repo_cfg: dict[str, str] | None = None,
    after_exit5: bool = False,
) -> None:
    """A commit built from `verified_ops` is recorded as the verified output; the
    worktree HEAD is then replaced (same parent) by one built from `tamper_ops`.
    The fingerprint check must refuse the tampered head, fresh or after an exit 5.
    """
    hub, worktree = _fp_repo(tmp_path, base_ops, repo_cfg=repo_cfg)
    _apply_ops(worktree, verified_ops)
    _git(worktree, "commit", "-m", "verified")
    beads = _beads(hub, worktree)
    if after_exit5:
        with pytest.raises(MergeError) as check_error:
            _run(beads, hub, runner=lambda _c, _w: 1)
        assert check_error.value.code == 5
    _git(worktree, "reset", "--hard", "HEAD~1")
    _apply_ops(worktree, tamper_ops)
    _git(worktree, "commit", "-m", "tampered")
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value).startswith("worktree HEAD")


def _set_global_git_config(monkeypatch, tmp_path: Path, pairs: dict[str, str]) -> Path:
    cfg = tmp_path / "gitconfig-global"
    cfg.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key, value in pairs.items():
        subprocess.run(["git", "config", "--file", str(cfg), key, value], check=True)
    return cfg


SUBMODULE_CONFIGS: dict[str, dict] = {
    "global-diff.submodule=log": {"global": {"diff.submodule": "log"}},
    "repo-diff.submodule=diff": {"repo": {"diff.submodule": "diff"}},
    "global-diff.ignoreSubmodules=all": {"global": {"diff.ignoreSubmodules": "all"}},
    "gitmodules-ignore=all": {"gitmodules": True},
}


@pytest.mark.parametrize("tamper_kind", ["pointer", "extra-file"])
@pytest.mark.parametrize("config_name", list(SUBMODULE_CONFIGS))
def test_submodule_config_cannot_hide_an_unverified_change(
    tmp_path: Path, monkeypatch, config_name: str, tamper_kind: str
) -> None:
    spec = SUBMODULE_CONFIGS[config_name]
    base_ops = [("write", "a.txt", b"a\n"), ("write", "z.txt", b"z\n"), ("gitlink", "sub", "1" * 40)]
    if spec.get("gitmodules"):
        base_ops.append(
            ("write", ".gitmodules", b'[submodule "sub"]\n\tpath = sub\n\turl = ./sub\n\tignore = all\n')
        )
    repo_cfg = spec.get("repo", {})
    if spec.get("global"):
        _set_global_git_config(monkeypatch, tmp_path, spec["global"])

    verified_ops = [("write", "a.txt", b"a2\n"), ("gitlink", "sub", "2" * 40)]
    if tamper_kind == "pointer":
        tamper_ops = [("write", "a.txt", b"a2\n"), ("gitlink", "sub", "3" * 40)]
    else:
        tamper_ops = [("write", "a.txt", b"a2\n"), ("gitlink", "sub", "2" * 40), ("write", "z.txt", b"EVIL\n")]

    _fp_case_refuses(tmp_path / "fresh", base_ops, verified_ops, tamper_ops, repo_cfg=repo_cfg)
    _fp_case_refuses(
        tmp_path / "after-exit5", base_ops, verified_ops, tamper_ops, repo_cfg=repo_cfg, after_exit5=True
    )


def test_nul_byte_after_diff_attribute_refuses(tmp_path: Path) -> None:
    base_ops = [("write", ".gitattributes", b"*.dat diff\n"), ("write", "x.dat", b"A\x00base\n")]
    verified_ops = [("write", "x.dat", b"A\x00GOOD\n")]
    tamper_ops = [("write", "x.dat", b"A\x00EVIL\n")]
    _fp_case_refuses(tmp_path / "fresh", base_ops, verified_ops, tamper_ops)
    _fp_case_refuses(tmp_path / "after-exit5", base_ops, verified_ops, tamper_ops, after_exit5=True)


def test_nul_byte_after_8200_bytes_refuses(tmp_path: Path) -> None:
    prefix = b"a\n" * 4100  # 8200 bytes of text before the NUL
    base_ops = [("write", "big.sh", prefix + b"x\x00base\n")]
    verified_ops = [("write", "big.sh", prefix + b"x\x00GOOD\n")]
    tamper_ops = [("write", "big.sh", prefix + b"x\x00EVIL\n")]
    _fp_case_refuses(tmp_path / "fresh", base_ops, verified_ops, tamper_ops)
    _fp_case_refuses(tmp_path / "after-exit5", base_ops, verified_ops, tamper_ops, after_exit5=True)


def test_binary_change_with_mode_change_on_other_path_refuses(tmp_path: Path) -> None:
    base_ops = [("write", "a.bin", b"\x00\x01"), ("write", "b.sh", b"b\n"), ("write", "c.sh", b"c\n")]
    verified_ops = [("write", "a.bin", b"\x00\x02"), ("chmod", "b.sh", 0o755)]
    tamper_ops = [("write", "a.bin", b"\x00\x02"), ("chmod", "c.sh", 0o755)]
    _fp_case_refuses(tmp_path / "fresh", base_ops, verified_ops, tamper_ops)
    _fp_case_refuses(tmp_path / "after-exit5", base_ops, verified_ops, tamper_ops, after_exit5=True)


def test_mode_only_change_on_other_path_refuses(tmp_path: Path) -> None:
    base_ops = [("write", "s.sh", b"echo s\n"), ("write", "t.sh", b"echo t\n")]
    verified_ops = [("chmod", "s.sh", 0o755)]
    tamper_ops = [("chmod", "t.sh", 0o755)]
    _fp_case_refuses(tmp_path / "fresh", base_ops, verified_ops, tamper_ops)
    _fp_case_refuses(tmp_path / "after-exit5", base_ops, verified_ops, tamper_ops, after_exit5=True)


def test_rename_to_different_target_refuses(tmp_path: Path) -> None:
    content = b"".join(b"line %d\n" % i for i in range(20))
    base_ops = [("write", "r.txt", content)]
    verified_ops = [("mv", "r.txt", "r2.txt")]
    tamper_ops = [("mv", "r.txt", "r3.txt")]
    _fp_case_refuses(tmp_path / "fresh", base_ops, verified_ops, tamper_ops)
    _fp_case_refuses(tmp_path / "after-exit5", base_ops, verified_ops, tamper_ops, after_exit5=True)


def test_hand_rebase_with_varied_changes_merges(tmp_path: Path, monkeypatch) -> None:
    """A true positive: a two-commit verified range (a mid-file edit, a mode
    change, a binary add and a rename, then an empty-file commit) still merges
    after a hand rebase onto a main that added lines at the top of the same
    file, under a hostile global diff config that reshapes prefixes, order,
    algorithm, renames, submodules and signatures. Trees do not depend on git
    config (h3c-fix7 item 1), so the config matrix shrinks to this one hostile
    case; an empty config is not tested separately.
    """
    order_file = tmp_path / "orderfile"
    order_file.write_text("*\n")
    _set_global_git_config(
        monkeypatch,
        tmp_path,
        {
            "diff.noprefix": "true",
            "diff.mnemonicPrefix": "true",
            "diff.algorithm": "histogram",
            "diff.renames": "copies",
            "diff.orderFile": str(order_file),
            "diff.submodule": "log",
            "diff.ignoreSubmodules": "all",
            "diff.relative": "true",
            "log.showSignature": "true",
        },
    )

    original = b"".join(b"line %02d\n" % i for i in range(60))
    lines = original.splitlines(keepends=True)
    lines[49] = b"line FIFTY\n"
    edited = b"".join(lines)

    base_ops = [("write", "sixty.txt", original), ("write", "mode.sh", b"echo hi\n"), ("write", "old.txt", b"old\n")]
    hub, worktree = _fp_repo(tmp_path, base_ops)

    verified_ops = [
        ("write", "sixty.txt", edited),
        ("chmod", "mode.sh", 0o755),
        ("write", "new.bin", b"\x00\x01\x02"),
        ("mv", "old.txt", "renamed.txt"),
    ]
    _apply_ops(worktree, verified_ops)
    _git(worktree, "commit", "-m", "verified")
    _apply_ops(worktree, [("write", "empty.txt", b"")])
    _git(worktree, "commit", "-m", "empty file")
    beads = _beads(hub, worktree)

    top = b"top1\ntop2\ntop3\ntop4\ntop5\n"
    (hub / "sixty.txt").write_bytes(top + original)
    _git(hub, "add", "sixty.txt")
    _git(hub, "commit", "-m", "advance main")

    _git(worktree, "rebase", "main")
    assert _run(beads, hub) == (0, "merged")


# ---------------------------------------------------------------- h3c-fix7 item 1:
# the fingerprint rule is blind to position; the replay rule (git merge-tree) is
# not. (a)-(d) are refusals a fingerprint compare missed or wrongly produced;
# (e)-(g) are true positives a fingerprint compare wrongly refused.


def test_edit_moved_to_identical_twin_block_refuses(tmp_path: Path) -> None:
    """(a) Two identical 7-line blocks: the verified commit sets `value = 2` in the
    first block, the worktree instead sets it in the second. A diff fingerprint
    with `@@` headers collapsed sees the same bytes either way; a tree replay does
    not, because the two resulting trees differ.
    """
    block = b"a\nb\nc\nvalue = 1\nd\ne\nf\n"
    edited_block = b"a\nb\nc\nvalue = 2\nd\ne\nf\n"
    base_ops = [("write", "twin.txt", block + block)]
    verified_ops = [("write", "twin.txt", edited_block + block)]
    tamper_ops = [("write", "twin.txt", block + edited_block)]
    _fp_case_refuses(tmp_path / "fresh", base_ops, verified_ops, tamper_ops)
    _fp_case_refuses(tmp_path / "after-exit5", base_ops, verified_ops, tamper_ops, after_exit5=True)


def test_addition_moved_elsewhere_with_zero_context_refuses(tmp_path: Path) -> None:
    """(b) A pure addition (one new line) moved to a different position in the
    same file, with `diff.context=0` set in the hub's own config (the config the
    review measured this defect under).
    """
    base_ops = [("write", "grow.txt", b"L1\nL2\nL3\nL4\nL5\n")]
    verified_ops = [("write", "grow.txt", b"L1\nL2\nNEW\nL3\nL4\nL5\n")]
    tamper_ops = [("write", "grow.txt", b"L1\nL2\nL3\nL4\nNEW\nL5\n")]
    repo_cfg = {"diff.context": "0"}
    _fp_case_refuses(tmp_path / "fresh", base_ops, verified_ops, tamper_ops, repo_cfg=repo_cfg)
    _fp_case_refuses(
        tmp_path / "after-exit5", base_ops, verified_ops, tamper_ops, repo_cfg=repo_cfg, after_exit5=True
    )


def test_extra_file_amended_after_real_rebase_refuses(tmp_path: Path) -> None:
    """(c) An extra file is amended into the commit after a real `git rebase main`."""
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    (hub / "other.txt").write_text("other\n")
    _git(hub, "add", "other.txt")
    _git(hub, "commit", "-m", "advance main")
    _git(worktree, "rebase", "main")
    (worktree / "extra.txt").write_text("extra\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "--amend", "--no-edit")
    new_head = _git(worktree, "rev-parse", "HEAD")
    old_commit = beads.beads["b1"].metadata["output_commit"]
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value) == f"worktree HEAD {new_head} is not the verified output_commit {old_commit}"


# (d) a conflicting replay (main rewrites the same lines the verified commit
# changed, worktree HEAD resolved by hand) refuses: already covered by
# test_hand_resolved_conflict_with_different_patch_refuses above, which runs
# this exact scenario through `helios merge` and asserts exit 2.


def _repo_with_base_content(tmp_path: Path, base_lines: list[str]) -> tuple[Path, Path]:
    """A hub and worktree that both start from the same multi-line `value.txt`,
    with no worktree commit yet beyond the shared base (unlike `_repo`, whose
    worktree already carries a one-line "change" commit).
    """
    hub = tmp_path / "repo"
    hub.mkdir(parents=True)
    _git(hub, "init", "-b", "main")
    _git(hub, "config", "user.email", "test@example.com")
    _git(hub, "config", "user.name", "Test")
    (hub / "value.txt").write_text("\n".join(base_lines) + "\n")
    (hub / ".beads").mkdir()
    (hub / ".beads" / "issues.jsonl").write_text("{}\n")
    _git(hub, "add", ".")
    _git(hub, "commit", "-m", "base")
    worktree = tmp_path / "worktree" / "b1"
    worktree.parent.mkdir()
    _git(hub, "worktree", "add", "-b", "worktree-b1", str(worktree), "main")
    return hub, worktree


def test_hand_rebase_edit_two_lines_away_merges(tmp_path: Path) -> None:
    """(e) A hand rebase where main edits 2 lines away from the verified edit
    still merges: the verified commit edits line 5, main independently edits
    line 3 of the same file before the worktree rebases onto it.
    """
    base_lines = [f"L{i}" for i in range(1, 11)]
    hub, worktree = _repo_with_base_content(tmp_path, base_lines)
    edited_lines = list(base_lines)
    edited_lines[4] = "L5-EDITED"
    (worktree / "value.txt").write_text("\n".join(edited_lines) + "\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "verified edit")
    beads = _beads(hub, worktree)

    hub_lines = list(base_lines)
    hub_lines[2] = "L3-MAIN"
    (hub / "value.txt").write_text("\n".join(hub_lines) + "\n")
    _git(hub, "add", "value.txt")
    _git(hub, "commit", "-m", "advance main near edit")

    _git(worktree, "rebase", "main")
    assert _run(beads, hub) == (0, "merged")


def test_hand_rebase_main_prepends_ten_lines_merges(tmp_path: Path) -> None:
    """(f) A hand rebase where main prepends 10 lines to the same file still merges."""
    base_lines = [f"L{i}" for i in range(1, 6)]
    hub, worktree = _repo_with_base_content(tmp_path, base_lines)
    edited_lines = base_lines[:-1] + ["L5-EDITED"]
    (worktree / "value.txt").write_text("\n".join(edited_lines) + "\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "verified edit")
    beads = _beads(hub, worktree)

    prefix = [f"TOP{i}" for i in range(1, 11)]
    (hub / "value.txt").write_text("\n".join(prefix + base_lines) + "\n")
    _git(hub, "add", "value.txt")
    _git(hub, "commit", "-m", "prepend ten lines")

    _git(worktree, "rebase", "main")
    assert _run(beads, hub) == (0, "merged")


def test_two_commit_range_squashed_on_new_main_merges(tmp_path: Path) -> None:
    """(g) A two-commit verified range, replayed as one squashed commit on the
    new main, still merges.
    """
    hub, worktree = _repo(tmp_path)
    (worktree / "value.txt").write_text("step1\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "--amend", "-m", "step one")
    (worktree / "extra.txt").write_text("step2\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "step two")
    beads = _beads(hub, worktree)

    (hub / "other.txt").write_text("other\n")
    _git(hub, "add", "other.txt")
    _git(hub, "commit", "-m", "advance main")

    _git(worktree, "reset", "--hard", "main")
    (worktree / "value.txt").write_text("step1\n")
    (worktree / "extra.txt").write_text("step2\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "squashed")

    assert _run(beads, hub) == (0, "merged")


# ---------------------------------------------------------------- h3c-fix6 item 2:
# the .beads/ branch diff is parsed with -z, so a quoted path does not escape it.


@pytest.mark.parametrize("name", [".beads/café.jsonl", '.beads/a"b.jsonl', ".beads/tab\there.jsonl"])
def test_beads_diff_with_quoted_path_still_refuses(tmp_path: Path, name: str) -> None:
    hub, worktree = _repo(tmp_path)
    path = worktree / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "quoted beads path")
    beads = _beads(hub, worktree)
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value) == "branch changes .beads/"


# ---------------------------------------------------------------- h3c-fix6 item 3:
# `_worktree_registered` compares Unicode NFC, so an NFC/NFD mismatch in a listed
# path does not read as unregistered.


def test_worktree_registered_matches_across_unicode_normalization_forms(tmp_path: Path) -> None:
    import helios.merge as merge_module

    hub_name = unicodedata.normalize("NFD", "hub-café")
    hub = tmp_path / hub_name
    hub.mkdir()
    _git(hub, "init", "-b", "main")
    _git(hub, "config", "user.email", "test@example.com")
    _git(hub, "config", "user.name", "Test")
    (hub / "value.txt").write_text("base\n")
    _git(hub, "add", ".")
    _git(hub, "commit", "-m", "base")
    worktree = hub / ".claude" / "worktrees" / "b1"
    worktree.parent.mkdir(parents=True)
    _git(hub, "worktree", "add", "-b", "worktree-b1", str(worktree), "main")
    _git(hub, "worktree", "lock", "--reason", "keep", str(worktree))

    nfc_query = Path(unicodedata.normalize("NFC", str(worktree)))
    nfd_query = Path(unicodedata.normalize("NFD", str(worktree)))
    assert merge_module._worktree_registered(hub, nfc_query) is True
    assert merge_module._worktree_registered(hub, nfd_query) is True
    assert merge_module._worktree_registered(hub, hub.parent / "not-there") is False


# ---------------------------------------------------------------- h3c-fix8 item 1:
# the verified-commit check replays the verified range commit by commit, not as one
# squashed `merge-tree` call. A squashed replay loses per-commit rename detection: a
# rename in one commit followed by a near-total rewrite in the next drops under
# git's whole-range similarity threshold even though a real `git rebase main` is
# clean, so the old rule refused work a rebase (hand or helios's own) produced.

_RENAME_LINES = b"".join(b"original line %02d\n" % i for i in range(40))
_RENAME_REWRITE = b"".join(
    (b"rewritten %02d\n" % i) if i >= 5 else (b"original line %02d\n" % i) for i in range(40)
)


def _rename_then_rewrite_repo(tmp_path: Path) -> tuple[Path, Path, FakeBeads]:
    """`f.txt` renamed to `g.txt` in one commit, then 35 of its 40 lines rewritten
    in the next (about 9 percent whole-range similarity), while main independently
    edits line 1 of the old path `f.txt`.
    """
    hub, worktree = _fp_repo(tmp_path, [("write", "f.txt", _RENAME_LINES)])
    _git(worktree, "mv", "f.txt", "g.txt")
    _git(worktree, "commit", "-m", "rename")
    _apply_ops(worktree, [("write", "g.txt", _RENAME_REWRITE)])
    _git(worktree, "commit", "-m", "rewrite")
    beads = _beads(hub, worktree)

    main_lines = _RENAME_LINES.split(b"\n")
    main_lines[1] = b"MAIN edited line 01"
    (hub / "f.txt").write_bytes(b"\n".join(main_lines))
    _git(hub, "add", "f.txt")
    _git(hub, "commit", "-m", "main edits the old path")
    return hub, worktree, beads


def test_rename_then_rewrite_while_main_edits_old_path_merges(tmp_path: Path) -> None:
    """(a) A hand rebase of the rename-then-rewrite range onto main's edit to the
    old path merges, and main's tree carries both main's edit and the rewrite.
    """
    hub, worktree, beads = _rename_then_rewrite_repo(tmp_path)
    _git(worktree, "rebase", "main")
    assert _run(beads, hub) == (0, "merged")

    tree = sorted(_git(hub, "ls-tree", "-r", "--name-only", "main").splitlines())
    assert tree == [".beads/issues.jsonl", "g.txt"]
    expected_lines = _RENAME_REWRITE.split(b"\n")
    expected_lines[1] = b"MAIN edited line 01"
    expected = b"\n".join(expected_lines).decode().strip()
    assert _git(hub, "show", "main:g.txt") == expected


def test_rename_then_rewrite_via_helios_rebase_then_rerun_merges(tmp_path: Path) -> None:
    """(b) helios's own rebase (step 3) produces the same shape; a first attempt
    that fails a check with exit 5 must not have its rebased HEAD refused by a
    rerun.
    """
    hub, worktree, beads = _rename_then_rewrite_repo(tmp_path)
    original_output_commit = beads.beads["b1"].metadata["output_commit"]
    with pytest.raises(MergeError) as error:
        _run(beads, hub, runner=lambda _c, _w: 1)
    assert error.value.code == 5
    rebased = _git(worktree, "rev-parse", "HEAD")
    assert rebased != original_output_commit

    assert _run(beads, hub) == (0, "merged")
    expected_lines = _RENAME_REWRITE.split(b"\n")
    expected_lines[1] = b"MAIN edited line 01"
    expected = b"\n".join(expected_lines).decode().strip()
    assert _git(hub, "show", "main:g.txt") == expected


def test_tamper_amended_onto_rebased_rename_rewrite_head_refuses(tmp_path: Path) -> None:
    """(c) A tamper amended onto that rebased HEAD refuses with exit 2."""
    hub, worktree, beads = _rename_then_rewrite_repo(tmp_path)
    _git(worktree, "rebase", "main")
    (worktree / "g.txt").write_bytes(_RENAME_REWRITE + b"EVIL\n")
    _git(worktree, "add", "g.txt")
    _git(worktree, "commit", "--amend", "--no-edit")
    new_head = _git(worktree, "rev-parse", "HEAD")
    old_commit = beads.beads["b1"].metadata["output_commit"]
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value) == f"worktree HEAD {new_head} is not the verified output_commit {old_commit}"


def test_orphan_worktree_branch_refuses_exit_two(tmp_path: Path) -> None:
    """(d) Unrelated histories (an orphan worktree branch) refuse with exit 2 and
    the ``worktree HEAD ...`` message, not exit 4: a failed or empty `merge-base`
    call is a refusal, not a git failure.
    """
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    _git(worktree, "checkout", "--orphan", "tmp-orphan")
    _git(worktree, "rm", "-rf", ".")
    (worktree / "evil.txt").write_text("evil\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "orphan")
    _git(worktree, "branch", "-M", "worktree-b1")
    new_head = _git(worktree, "rev-parse", "HEAD")
    old_commit = beads.beads["b1"].metadata["output_commit"]
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value) == f"worktree HEAD {new_head} is not the verified output_commit {old_commit}"


def test_replace_ref_on_range_commit_does_not_change_outcome(tmp_path: Path) -> None:
    """(e) A `git replace` ref planted on one commit inside the verified range,
    after a clean ordinary rebase has already run, does not change the outcome:
    `--no-replace-objects` guards every git call the replay makes.
    """
    hub, worktree = _repo(tmp_path)
    (worktree / "extra.txt").write_text("one\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "c1")
    c1 = _git(worktree, "rev-parse", "HEAD")
    (worktree / "extra.txt").write_text("one\ntwo\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "c2")
    out = _git(worktree, "rev-parse", "HEAD")
    beads = _beads(hub, worktree)
    beads.beads["b1"].metadata["output_commit"] = out
    _new_envelope(hub, worktree)

    (hub / "other.txt").write_text("other\n")
    _git(hub, "add", "other.txt")
    _git(hub, "commit", "-m", "advance main")
    _git(worktree, "rebase", "main")

    _git(worktree, "checkout", "-b", "evil-branch", "main")
    (worktree / "extra.txt").write_text("EVIL\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "evil")
    evil = _git(worktree, "rev-parse", "HEAD")
    _git(worktree, "checkout", "worktree-b1")
    _git(worktree, "branch", "-D", "evil-branch")
    _git(hub, "replace", c1, evil)

    assert _run(beads, hub) == (0, "merged")
    assert _git(hub, "show", "main:extra.txt") == "one\ntwo"
    assert _git(hub, "show", "main:other.txt") == "other"


# ---------------------------------------------------------------- hel-tq1:
# the scratch `commit-tree` call the replay makes keeps `GIT_AUTHOR_*` and
# `GIT_COMMITTER_*` out of its environment, so a hostile or malformed value
# inherited from the hub's environment cannot break a legitimate merge.


def _two_commit_replay_repo(tmp_path: Path) -> tuple[Path, Path, FakeBeads]:
    """A two-commit verified range, rebased onto a main that has since moved, so
    HEAD no longer equals `output_commit` and the verified-commit check must
    replay the range commit by commit (the scratch `commit-tree` call runs at
    least once) rather than take the HEAD-equals-output_commit shortcut.
    """
    hub, worktree = _repo(tmp_path)
    (worktree / "extra.txt").write_text("one\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "c1")
    (worktree / "extra.txt").write_text("one\ntwo\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "c2")
    out = _git(worktree, "rev-parse", "HEAD")
    beads = _beads(hub, worktree)
    beads.beads["b1"].metadata["output_commit"] = out
    _new_envelope(hub, worktree)

    (hub / "other.txt").write_text("other\n")
    _git(hub, "add", "other.txt")
    _git(hub, "commit", "-m", "advance main")
    _git(worktree, "rebase", "main")
    return hub, worktree, beads


def test_replay_merges_with_empty_git_committer_name(tmp_path: Path, monkeypatch) -> None:
    """A hostile empty `GIT_COMMITTER_NAME` in the environment does not break the
    scratch `commit-tree` call the replay makes; git would otherwise refuse an
    empty ident name and the merge would die with exit 4.
    """
    hub, worktree, beads = _two_commit_replay_repo(tmp_path)
    monkeypatch.setenv("GIT_COMMITTER_NAME", "")
    assert _run(beads, hub) == (0, "merged")
    assert _git(hub, "show", "main:extra.txt") == "one\ntwo"


def test_replay_merges_with_malformed_git_author_date(tmp_path: Path, monkeypatch) -> None:
    """A malformed `GIT_AUTHOR_DATE` in the environment does not break the
    scratch `commit-tree` call the replay makes; git would otherwise refuse an
    unparsable date and the merge would die with exit 4.
    """
    hub, worktree, beads = _two_commit_replay_repo(tmp_path)
    monkeypatch.setenv("GIT_AUTHOR_DATE", "not-a-date")
    assert _run(beads, hub) == (0, "merged")
    assert _git(hub, "show", "main:extra.txt") == "one\ntwo"


def test_replay_merges_with_valid_author_and_committer_env(tmp_path: Path, monkeypatch) -> None:
    """Valid `GIT_AUTHOR_*`/`GIT_COMMITTER_*` values in the environment do not
    change the outcome either: they can only ever change a scratch commit's
    sha, never the tree the replay compares.
    """
    hub, worktree, beads = _two_commit_replay_repo(tmp_path)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Someone")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "someone@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Someone Else")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "else@example.com")
    assert _run(beads, hub) == (0, "merged")
    assert _git(hub, "show", "main:extra.txt") == "one\ntwo"


# ---------------------------------------------------------------- hel-z3b: merge's
# default `input_hashes` recomputes the VERIFY bead's own input hashes the same way
# a real `helios run` computes them (SPEC 7.1 step 6, 8.5), not the impl bead's, and
# not from bead metadata nothing ever writes (the defect the wave 3 end-to-end run
# caught and the unit tests missed, since they always injected the hash function).
# These build a real hub and run a real verify-code bead through the fake harness,
# so the envelope's `input_hashes` come from the real prompt-assembly code path, and
# exercise `helios merge` with no injected hash function at all.


_REAL_VERIFY_REPORT = {
    "status": "done",
    "summary": "ok",
    "findings": [
        {
            "id": "f1",
            "claim": "the merge fix works",
            "verdict": "verified",
            "method": "review",
            "scope": "universal",
        }
    ],
}


def _real_evidence_hub(tmp_path: Path, monkeypatch) -> tuple[Path, FakeBeads, ProjectConfig]:
    """A real hub: a real impl worktree and commit, and a real verify-code bead
    run through the fake harness, so its envelope.json's `input_hashes` are
    whatever `helios run` actually recorded, not hand-written.
    """
    hub = tmp_path / "hub"
    hub.mkdir()
    _git(hub, "init", "-b", "main")
    _git(hub, "config", "user.email", "t@t")
    _git(hub, "config", "user.name", "t")
    (hub / "value.txt").write_text("base\n")
    (hub / ".gitignore").write_text(".helios/\n.claude/worktrees/\n")
    (hub / "docs").mkdir()
    (hub / "docs" / "NOTE.md").write_text("# Note\n\noriginal text\n")
    for kind in ("impl", "verify-code"):
        (hub / "skills" / kind).mkdir(parents=True)
        (hub / "skills" / kind / "SKILL.md").write_text(
            f"---\nname: {kind}\n---\n\n# {kind} bead\n\nDo the work.\n"
        )
    (hub / "AGENTS.md").write_text(
        "# helios\n\n## Worker contract\n\nWork exactly one bead.\n\n## Orchestrator\n\nMerges.\n"
    )
    _git(hub, "add", ".")
    _git(hub, "commit", "-m", "init")

    worktree = hub / ".claude" / "worktrees" / "b1"
    worktree.parent.mkdir(parents=True)
    _git(hub, "worktree", "add", "-b", "worktree-b1", str(worktree), "main")
    _git(hub, "worktree", "lock", "--reason", "keep", str(worktree))
    (worktree / "value.txt").write_text("merged\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "change")
    output_commit = _git(worktree, "rev-parse", "HEAD")

    impl = Bead(
        id="b1",
        kind="impl",
        unit="u1",
        files=["value.txt"],
        test="true",
        metadata={"output_commit": output_commit},
    )
    verify = Bead(
        id="v1",
        kind="verify-code",
        unit="u1",
        parent="b1",
        files=["tools/verify/u1/"],
        test="true",
        docs=["docs/NOTE.md"],
        labels=["unit:u1"],
    )
    beads = FakeBeads([impl, verify])

    script = tmp_path / "fake_script.json"
    script.write_text(
        json.dumps(
            {
                "exit_code": 0,
                "sleep_s": 0,
                "stdout": "x",
                "session_id": "s1",
                "report": _REAL_VERIFY_REPORT,
            }
        )
    )
    monkeypatch.setenv("HELIOS_FAKE_SCRIPT", str(script))

    cfg = Config(hub=hub)
    rc = run_mod.run_one("v1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    assert rc == 0, "setup: the verify bead must run and verify cleanly"
    assert "v1" in beads.closed
    assert beads.beads["v1"].metadata.get("verdict") == "verified"
    # FakeBeads.close() records the close (`.closed`) without touching
    # `.status` (a write-back convention, see FakeBeads.close); merge only
    # reads `.status`, so set it here the way a real `bd close` would.
    beads.beads["v1"].status = "closed"
    beads.beads["b1"].status = "closed"
    return hub, beads, cfg.project


def test_real_hub_no_injected_hashes_merges(tmp_path: Path, monkeypatch) -> None:
    """Acceptance item 1: a real hub, a real impl bead, a real verify-code bead
    and a real envelope, no injected hash function: `helios merge` succeeds.
    """
    hub, beads, project = _real_evidence_hub(tmp_path, monkeypatch)
    code, message = merge_bead(
        hub, "b1", project=project, beads=beads, check_runner=lambda _command, _cwd: 0
    )
    assert (code, message) == (0, "merged")


def test_real_hub_doc_change_after_envelope_refuses_as_stale(tmp_path: Path, monkeypatch) -> None:
    """Acceptance item 2: changing a doc the verify prompt used, after the
    envelope was written, refuses as stale and writes nothing.
    """
    hub, beads, project = _real_evidence_hub(tmp_path, monkeypatch)
    (hub / "docs" / "NOTE.md").write_text("# Note\n\nchanged after the verify envelope\n")
    _git(hub, "add", "docs/NOTE.md")
    _git(hub, "commit", "-m", "doc changed after verify")
    before_main = _git(hub, "rev-parse", "main")
    before_log = len(beads.argv_log)
    with pytest.raises(MergeError) as error:
        merge_bead(hub, "b1", project=project, beads=beads, check_runner=lambda _command, _cwd: 0)
    assert str(error.value) == "verify envelope is stale"
    assert error.value.code == 2
    assert beads.argv_log[before_log:] == []
    assert _git(hub, "rev-parse", "main") == before_main


def test_real_hub_prompt_only_change_still_merges(tmp_path: Path, monkeypatch) -> None:
    """Acceptance item 3: changing only the recorded `prompt` hash (so only that
    key differs) still merges, since `prompt` is exempt from the comparison.
    """
    hub, beads, project = _real_evidence_hub(tmp_path, monkeypatch)
    envelope_path = hub / ".helios" / "runs" / "v1" / "attempt-1" / "envelope.json"
    recorded = Envelope.model_validate_json(envelope_path.read_text())
    assert "prompt" in recorded.input_hashes
    changed = recorded.model_copy(
        update={"input_hashes": {**recorded.input_hashes, "prompt": "not-the-real-prompt-hash"}}
    )
    envelope_path.write_text(changed.model_dump_json())
    code, message = merge_bead(
        hub, "b1", project=project, beads=beads, check_runner=lambda _command, _cwd: 0
    )
    assert (code, message) == (0, "merged")


def test_real_hub_missing_recorded_key_refuses_as_stale(tmp_path: Path, monkeypatch) -> None:
    """Acceptance item 4: a key present on the recomputed side but missing from
    the recorded side counts as different and refuses.
    """
    hub, beads, project = _real_evidence_hub(tmp_path, monkeypatch)
    envelope_path = hub / ".helios" / "runs" / "v1" / "attempt-1" / "envelope.json"
    recorded = Envelope.model_validate_json(envelope_path.read_text())
    assert "bead" in recorded.input_hashes
    trimmed = {key: value for key, value in recorded.input_hashes.items() if key != "bead"}
    changed = recorded.model_copy(update={"input_hashes": trimmed})
    envelope_path.write_text(changed.model_dump_json())
    before_main = _git(hub, "rev-parse", "main")
    before_log = len(beads.argv_log)
    with pytest.raises(MergeError) as error:
        merge_bead(hub, "b1", project=project, beads=beads, check_runner=lambda _command, _cwd: 0)
    assert str(error.value) == "verify envelope is stale"
    assert error.value.code == 2
    assert beads.argv_log[before_log:] == []
    assert _git(hub, "rev-parse", "main") == before_main


def test_real_hub_dry_run_reports_success_not_staleness(tmp_path: Path, monkeypatch) -> None:
    """Acceptance item 5 (dry-run half): `--dry-run` on the real hub of item 1
    reports success rather than staleness; the existing injected-hash tests
    (the rest of this file) stay green.
    """
    hub, beads, project = _real_evidence_hub(tmp_path, monkeypatch)
    assert merge_bead(hub, "b1", project=project, beads=beads, dry_run=True) == (
        0,
        "would rebase, test, merge, push, and remove",
    )
    assert _git(hub, "show", "main:value.txt") == "base"


# ---------------------------------------------------------------- hel-nkk: a branch
# can carry extra commits whose net tree effect is zero and still replay to the
# verified tree, so the range sizes are compared too, not just the final trees.


def test_add_then_revert_pair_refuses_longer_range(tmp_path: Path) -> None:
    """A rebased HEAD carries an add-then-revert commit pair on top of the
    verified change: 3 commits against a 1-commit verified range. Its final tree
    matches the verified tree exactly, but `head_base..HEAD` holds more commits
    than `output_base..output_commit`, so it refuses, writes nothing, and the
    blob it added never reaches main's history.
    """
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    old_commit = beads.beads["b1"].metadata["output_commit"]

    _git(worktree, "reset", "--hard", "main")
    (worktree / "value.txt").write_text("merged\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "merged value")
    (worktree / "blob.txt").write_text("evil\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "add blob")
    _git(worktree, "rm", "blob.txt")
    _git(worktree, "commit", "-m", "revert blob")
    new_head = _git(worktree, "rev-parse", "HEAD")
    assert new_head != old_commit
    assert _git(worktree, "rev-parse", "HEAD^{tree}") == _git(hub, "rev-parse", f"{old_commit}^{{tree}}")

    before_main = _git(hub, "rev-parse", "main")
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value) == f"worktree HEAD {new_head} is not the verified output_commit {old_commit}"
    assert beads.argv_log == []
    assert _git(hub, "rev-parse", "main") == before_main
    assert "blob.txt" not in _git(hub, "ls-tree", "-r", "--name-only", "main").splitlines()
    assert "blob.txt" not in _git(hub, "rev-list", "--objects", "main").splitlines()


# The range-size check does not disturb ordinary equal-or-fewer-commit replays:
# a plain rebase (1 commit against 1) is `test_hand_rebase_onto_newer_main_still_merges`,
# a squashed hand rebase (1 against 2) is `test_two_commit_range_squashed_on_new_main_merges`,
# and a rename chain (2 against 2) is `test_rename_then_rewrite_while_main_edits_old_path_merges`,
# all already asserting exit 0; the fix keeps them green as is.


# ---------------------------------------------------------------- hel-cbx: a committed
# merge driver can steer `git merge-tree`'s replay unless the check isolates the
# global and system git config and refuses a driver defined in the hub's own
# repository config outright.


def _write_evil_driver(path: Path) -> Path:
    """A merge driver that ignores its real merge inputs and always overwrites the
    result with a fixed payload matching what a tampered HEAD carries: if a
    driver can take part in the replay, this makes an unverified HEAD look
    verified.
    """
    script = path / "evil-driver.sh"
    script.write_text('#!/bin/sh\nprintf "EVIL PAYLOAD\\n" > "$2"\nexit 0\n')
    script.chmod(0o755)
    return script


def test_global_merge_driver_cannot_fool_the_replay(tmp_path: Path, monkeypatch) -> None:
    """A `.gitattributes` committed in the base sets `merge=evil` on `evil.dat`; a
    driver for `evil` is defined only in the global git config and always
    overwrites its result with the same payload the attacker's HEAD carries. Main
    advances with a conflicting edit to `evil.dat` between the verified commit and
    the attacker's HEAD, so replaying the verified commit onto `head_base` needs
    a real three-way merge of `evil.dat`, exactly where a driver could substitute
    forged content. `GIT_CONFIG_GLOBAL=/dev/null` on every git command of the
    check keeps this driver out of the replay, so the conflict surfaces as a real
    conflict and the tamper still refuses.
    """
    driver = _write_evil_driver(tmp_path)
    _set_global_git_config(monkeypatch, tmp_path, {"merge.evil.driver": f"{driver} %O %A %B"})

    hub = tmp_path / "repo"
    hub.mkdir()
    _git(hub, "init", "-b", "main")
    _git(hub, "config", "user.email", "test@example.com")
    _git(hub, "config", "user.name", "Test")
    (hub / ".beads").mkdir()
    (hub / ".beads" / "issues.jsonl").write_text("{}\n")
    (hub / "evil.dat").write_text("orig\n")
    (hub / ".gitattributes").write_text("evil.dat merge=evil\n")
    _git(hub, "add", ".")
    _git(hub, "commit", "-m", "base")
    worktree = tmp_path / "worktree" / "b1"
    worktree.parent.mkdir()
    _git(hub, "worktree", "add", "-b", "worktree-b1", str(worktree), "main")
    _git(hub, "worktree", "lock", "--reason", "keep", str(worktree))

    (worktree / "evil.dat").write_text("GOOD\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "verified change")
    beads = _beads(hub, worktree)
    old_commit = beads.beads["b1"].metadata["output_commit"]

    (hub / "evil.dat").write_text("MAIN\n")
    _git(hub, "add", "evil.dat")
    _git(hub, "commit", "-m", "advance main")

    _git(worktree, "reset", "--hard", "main")
    (worktree / "evil.dat").write_text("EVIL PAYLOAD\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "tampered")
    new_head = _git(worktree, "rev-parse", "HEAD")

    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value) == f"worktree HEAD {new_head} is not the verified output_commit {old_commit}"


def test_repo_config_merge_driver_refuses_before_replay(tmp_path: Path) -> None:
    """Config isolation alone does not help when the driver is defined in the
    hub's own repository config, reachable by a worker because a worktree shares
    the hub's `.git/config`. `helios merge` refuses immediately, naming the
    driver, before attempting any replay.
    """
    driver = _write_evil_driver(tmp_path)
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    _git(hub, "config", "merge.evil.driver", f"{driver} %O %A %B")
    (hub / "other.txt").write_text("other\n")
    _git(hub, "add", "other.txt")
    _git(hub, "commit", "-m", "advance main")
    _git(worktree, "rebase", "main")

    before_main = _git(hub, "rev-parse", "main")
    with pytest.raises(MergeError) as error:
        _run(beads, hub)
    assert error.value.code == 2
    assert str(error.value) == "hub git config defines merge driver evil"
    assert beads.argv_log == []
    assert _git(hub, "rev-parse", "main") == before_main


def test_no_merge_driver_configured_is_unaffected(tmp_path: Path) -> None:
    """A hub with no merge driver configured is unaffected: the driver-detection
    check finds nothing and a plain hand rebase still merges (SPEC 12 step 2).
    """
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    (hub / "other.txt").write_text("other\n")
    _git(hub, "add", "other.txt")
    _git(hub, "commit", "-m", "advance main")
    _git(worktree, "rebase", "main")
    assert _run(beads, hub) == (0, "merged")
