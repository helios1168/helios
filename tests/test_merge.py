"""Merge command tests (SPEC section 12)."""

from __future__ import annotations

import subprocess
import fcntl
import shutil
from argparse import Namespace
from pathlib import Path

import pytest

from helios.beads import Bead, FakeBeads
from helios.config import ProjectConfig
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
    (worktree / "value.txt").write_text("merged\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "change")
    return hub, worktree


def _beads(hub: Path, worktree: Path) -> FakeBeads:
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
            input_hashes={},
            execution_status=ExecutionStatus.COMPLETED,
            report=AgentReport(status=WorkStatus.DONE, summary="ok"),
        ).model_dump_json()
    )
    return FakeBeads([impl, verify])


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
    beads = _beads(hub, worktree)
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
    beads = _beads(hub, worktree)
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


def test_refusal_removes_the_lock_it_created(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    beads.beads["v1"].metadata["verdict"] = "inconclusive"
    with pytest.raises(MergeError):
        _run(beads, hub)
    assert not (hub / ".helios" / "runs" / "b1").exists()


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


def test_clean_check_ignores_beads_export_in_hub_and_worktree(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    import helios.merge as merge_module

    assert merge_module._clean(hub) is True
    assert merge_module._clean(worktree) is True
    (hub / ".beads" / "issues.jsonl").write_text('{"dirtied": true}\n')
    (worktree / ".beads" / "issues.jsonl").write_text('{"dirtied": true}\n')
    assert merge_module._clean(hub) is True
    assert merge_module._clean(worktree) is True


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
    beads = _beads(hub, worktree)
    beads.beads["v1"].metadata["verdict"] = "inconclusive"
    import helios.commands.merge as command
    from helios.config import Config

    monkeypatch.setattr(command, "load", lambda _cwd: Config(hub=hub, project=_project()))
    monkeypatch.setattr(command, "Beads", lambda _hub: beads)
    assert command.run(Namespace(bead="b1", dry_run=False)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "verify evidence is not closed and verified\n"
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
