"""Beads wrapper and write-back tests (SPEC §7.1 step 1, §7.5)."""

from __future__ import annotations

import inspect
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from helios.beads import (
    Bead,
    BeadNotFound,
    Beads,
    BeadsLike,
    Comment,
    FakeBeads,
    apply_writeback,
    comment_has,
    decode_metadata,
    plan_writeback,
)

HEL_A6Z = "hel-a6z"


def test_metadata_json_strings_are_decoded() -> None:
    raw = {"files": '["src/a.py"]', "attempt": "3", "author": "codex", "unit": "null"}
    assert decode_metadata(raw) == {
        "files": ["src/a.py"],
        "attempt": 3,
        "author": "codex",
        "unit": None,
    }


def test_bead_from_show_reads_metadata_and_labels() -> None:
    payload = {
        "id": HEL_A6Z,
        "title": "t",
        "description": "d",
        "status": "in_progress",
        "labels": ["kind:impl"],
        "metadata": {
            "kind": "impl",
            "files": '["src/helios/config.py"]',
            "test": "uv run pytest -q",
            "docs": ["docs/SPEC.md#5."],
            "memories": "[]",
            "author": "opencode",
        },
    }
    bead = Bead.from_show(payload)
    assert bead.files == ["src/helios/config.py"]
    assert bead.test == "uv run pytest -q"
    assert bead.docs == ["docs/SPEC.md#5."]
    assert bead.author == "opencode"


def test_bead_from_show_renders_non_string_scalars_as_json_text() -> None:
    payload = {
        "id": "b1",
        "status": "open",
        "metadata": {"test": True, "accept": 3, "unit": None, "author": False},
    }
    bead = Bead.from_show(payload)
    assert bead.test == "true"
    assert bead.accept == "3"
    assert bead.unit is None
    assert bead.author == "false"


def test_beads_and_fakebeads_provide_every_beadslike_method() -> None:
    """`BeadsLike` is the interface control.py and others depend on; both concrete
    implementations must still provide each of its methods (hel-f8w item 1).
    """
    protocol_methods = [
        name
        for name, _ in inspect.getmembers(BeadsLike, predicate=inspect.isfunction)
        if not name.startswith("_")
    ]
    assert "list" in protocol_methods
    for name in protocol_methods:
        assert callable(getattr(Beads, name, None)), name
        assert callable(getattr(FakeBeads, name, None)), name


def test_replay_skips_existing_kind_and_marker() -> None:
    fake = FakeBeads([Bead(id="b1")])
    plan = plan_writeback(
        attempt_id="b1#1",
        bead_kind="impl",
        harness="codex",
        session_id="s1",
        worktree="/wt",
        attempt=1,
        execution_status="completed",
        verdict=None,
        output_commit=None,
        report_status="done",
        report_summary="did it",
        learned=["a", "b"],
        missing_context=[],
        followups=[],
        question=None,
        checks_passed=True,
    )
    assert apply_writeback(fake, "b1", plan) == 3
    assert fake.closed["b1"] == "did it"
    assert fake.beads["b1"].metadata["session"] == "codex:s1"
    # Replay is idempotent: no new comments, metadata write repeats harmlessly.
    assert apply_writeback(fake, "b1", plan) == 0
    assert len(fake.comments("b1")) == 3


def test_non_closing_plan_records_run_state() -> None:
    fake = FakeBeads([Bead(id="b2")])
    plan = plan_writeback(
        attempt_id="b2#1",
        bead_kind="verify-code",
        harness="claude",
        session_id=None,
        worktree="/wt",
        attempt=1,
        execution_status="completed",
        verdict="inconclusive",
        output_commit=None,
        report_status="done",
        report_summary="not yet",
        learned=[],
        missing_context=["x"],
        followups=[],
        question="which?",
        checks_passed=True,
    )
    assert not plan.close
    assert plan.run_state == "waiting"
    assert any(t.startswith("question: [b2#1]") for t in plan.comments)
    apply_writeback(fake, "b2", plan)
    assert "b2" not in fake.closed
    assert fake.states["b2"]["run"] == "waiting"


def test_event_comment_writes_dash_for_missing_status_and_summary() -> None:
    """SPEC §7.5: `event: [id] <execution_status> <status> <summary>`; a missing
    report status or summary writes `-`, never the literal `None` (hel-f8w item 3).
    """
    plan = plan_writeback(
        attempt_id="b#1",
        bead_kind="impl",
        harness="codex",
        session_id=None,
        worktree="/wt",
        attempt=1,
        execution_status="crashed",
        verdict=None,
        output_commit=None,
        report_status=None,
        report_summary="",
        learned=[],
        missing_context=[],
        followups=[],
        question=None,
        checks_passed=False,
    )
    assert plan.comments[0] == "event: [b#1] crashed - -"


def test_event_comment_replay_recognizes_old_format_marker() -> None:
    """A comment written before this change (with the literal `None`) still counts
    as the same `[id]` marker, so replay after it writes nothing new for `event`.
    """
    fake = FakeBeads([Bead(id="b1")])
    fake.add_comment("b1", "event: [b1#1] crashed None None")
    plan = plan_writeback(
        attempt_id="b1#1",
        bead_kind="impl",
        harness="codex",
        session_id=None,
        worktree="/wt",
        attempt=1,
        execution_status="crashed",
        verdict=None,
        output_commit=None,
        report_status=None,
        report_summary="",
        learned=[],
        missing_context=[],
        followups=[],
        question=None,
        checks_passed=False,
    )
    assert apply_writeback(fake, "b1", plan) == 0
    assert len(fake.comments("b1")) == 1


def test_comment_has_matches_kind_and_marker() -> None:
    comments = [Comment(id="c", issue_id="b", author="a", text="learned: [b#1#2] x")]
    assert comment_has(comments, "learned", "[b#1#2]")
    assert not comment_has(comments, "learned", "[b#1#3]")
    assert not comment_has(comments, "followup", "[b#1#2]")


def test_comment_has_ignores_quoted_markers() -> None:
    comments = [
        Comment(
            id="c", issue_id="hel-x", author="a",
            text="learned: [hel-x#2#1] see [hel-x#11] for context",
        )
    ]
    assert comment_has(comments, "learned", "[hel-x#2#1]")
    assert not comment_has(comments, "learned", "[hel-x#11]")


def test_fake_create_round_trips_labels_and_metadata() -> None:
    fake = FakeBeads()
    bead_id = fake.create(
        "probe",
        labels=["kind:impl", "unit:x"],
        metadata={"s": "true", "n": None, "flag": True, "files": ["a.py"]},
    )
    bead = fake.show(bead_id)
    assert bead.labels == ["kind:impl", "unit:x"]
    assert bead.kind == "impl"
    assert bead.metadata["s"] == "true"
    assert bead.metadata["n"] is None
    assert bead.metadata["flag"] is True
    assert bead.files == ["a.py"]


def test_fake_dep_add_is_idempotent() -> None:
    fake = FakeBeads()
    fake.dep_add("b1", "b2")
    fake.dep_add("b1", "b2")
    assert fake.deps["b1"] == {"b2"}


def test_fake_list_filters_by_labels_and_status() -> None:
    fake = FakeBeads(
        [
            Bead(id="b1", labels=["unit:x", "kind:impl"], status="open"),
            Bead(id="b2", labels=["unit:x", "kind:verify"], status="closed"),
            Bead(id="b3", labels=["unit:y"], status="open"),
        ]
    )
    assert [b.id for b in fake.list(labels=["unit:x"])] == ["b1", "b2"]
    assert [b.id for b in fake.list(labels=["unit:x"], status="open")] == ["b1"]


def test_fake_remember_recall_and_memories() -> None:
    fake = FakeBeads()
    assert fake.recall("missing") is None
    fake.remember("k", "v1")
    fake.remember("k", "v2")
    assert fake.recall("k") == "v2"
    assert fake.memories() == {"k": "v2"}


def test_fake_ready_requires_every_label_and_no_open_blocker() -> None:
    fake = FakeBeads(
        [
            Bead(id="b1", labels=["unit:x", "kind:impl"], status="open"),
            Bead(id="b2", labels=["unit:x"], status="open"),
            Bead(id="b3", labels=["unit:x", "kind:impl"], status="closed"),
            Bead(id="g1", labels=[], status="open"),
        ]
    )
    fake.dep_add("b2", "g1")
    assert [b.id for b in fake.ready(labels=["unit:x"])] == ["b1"]
    fake.close("g1", "resolved")
    fake.beads["g1"].status = "closed"
    assert {b.id for b in fake.ready(labels=["unit:x"])} == {"b1", "b2"}


def test_fake_add_label_is_idempotent() -> None:
    fake = FakeBeads([Bead(id="b1", labels=["kind:impl"])])
    fake.add_label("b1", "unit:x")
    fake.add_label("b1", "unit:x")
    assert fake.beads["b1"].labels == ["kind:impl", "unit:x"]


def test_fake_gate_list_and_gate_blocks() -> None:
    fake = FakeBeads(
        [
            Bead(id="g1", metadata={"issue_type": "gate"}, status="open"),
            Bead(id="b1"),
            Bead(id="b2"),
        ]
    )
    fake.dep_add("b1", "g1")
    fake.dep_add("b2", "g1")
    assert [g["id"] for g in fake.gate_list()] == ["g1"]
    assert fake.gate_blocks("g1") == ["b1", "b2"]


def test_fake_show_raises_bead_not_found_for_unknown_id() -> None:
    fake = FakeBeads([Bead(id="b1")])
    with pytest.raises(BeadNotFound) as exc_info:
        fake.show("missing")
    assert exc_info.value.bead_id == "missing"


BD = shutil.which("bd")


def _init_bd_repo(tmp_path: Path) -> None:
    """`git init` then `bd init --non-interactive` in a fresh directory."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["bd", "init", "--non-interactive"], cwd=tmp_path, check=True, capture_output=True
    )


def _create_with_id(tmp_path: Path, title: str, bead_id: str) -> None:
    """`bd create --id <bead_id> --force`, so the id need not match the db's own prefix."""
    subprocess.run(
        ["bd", "create", "--title", title, "--type", "task", "--id", bead_id, "--force"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )


def _write_shim_bd(tmp_path: Path, script: str) -> str:
    """A fake `bd` executable whose whole body is `script`; returns its path."""
    shim = tmp_path / "bd"
    shim.write_text(f"#!/bin/sh\n{script}\n")
    shim.chmod(0o755)
    return str(shim)


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_show_round_trip(tmp_path: Path) -> None:
    subprocess.run(["bd", "init"], cwd=tmp_path, check=True, capture_output=True)
    created = subprocess.run(
        ["bd", "create", "--title", "probe", "--type", "task", "--json"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    bead_id = json.loads(created.stdout)["id"] if created.stdout.strip().startswith("{") else created.stdout.strip().split()[-1]
    beads = Beads(tmp_path)
    bead = beads.show(bead_id)
    assert bead.id == bead_id
    assert bead.title == "probe"


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_show_raises_bead_not_found_for_missing_id(tmp_path: Path) -> None:
    """bd exits 1 with stdout `{"error": "no issues found matching the provided IDs", ...}`
    and stderr `Error fetching <id>: no issue found matching "<id>"` (checked against a
    real bd 1.2.2 in a temp repo).
    """
    _init_bd_repo(tmp_path)
    beads = Beads(tmp_path)
    with pytest.raises(BeadNotFound) as exc_info:
        beads.show("no-such-bead")
    assert exc_info.value.bead_id == "no-such-bead"


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_show_raises_bead_not_found_for_partial_id(tmp_path: Path) -> None:
    """A prefix that resolves to exactly one bead still exits 0, but the returned
    bead's id does not equal the requested (partial) id, so `show` treats it as
    not found rather than silently returning the wrong bead.
    """
    _init_bd_repo(tmp_path)
    _create_with_id(tmp_path, "probe", "zz-partial1")
    beads = Beads(tmp_path)
    with pytest.raises(BeadNotFound) as exc_info:
        beads.show("zz-partial")
    assert exc_info.value.bead_id == "zz-partial"
    # the exact id still resolves
    assert beads.show("zz-partial1").id == "zz-partial1"


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_show_raises_bead_not_found_for_ambiguous_prefix(tmp_path: Path) -> None:
    """An ambiguous prefix exits 1 with the same stdout error body as a missing id;
    only bd's stderr message differs (`ambiguous ID "<id>" matches N issues: [...]`,
    checked against a real bd 1.2.2 in a temp repo).
    """
    _init_bd_repo(tmp_path)
    _create_with_id(tmp_path, "A", "zz-amb1")
    _create_with_id(tmp_path, "B", "zz-amb2")
    beads = Beads(tmp_path)
    with pytest.raises(BeadNotFound) as exc_info:
        beads.show("zz-amb")
    assert exc_info.value.bead_id == "zz-amb"


def test_shim_bd_show_raises_runtime_error_when_locked(tmp_path: Path) -> None:
    """A real failure (for example a locked database) keeps raising `RuntimeError`,
    not `BeadNotFound`, even though bd still exits 1.
    """
    shim = _write_shim_bd(tmp_path, 'echo "Error: database is locked" >&2\nexit 1\n')
    beads = Beads(tmp_path, binary=shim)
    with pytest.raises(RuntimeError, match="database is locked"):
        beads.show("any-id")


def test_shim_bd_show_raises_runtime_error_for_malformed_success_payload(
    tmp_path: Path,
) -> None:
    """`bd show` exiting 0 with a bead object that has no `id` field is malformed,
    not a not-found signal, so it raises `RuntimeError` rather than `BeadNotFound`.
    """
    shim = _write_shim_bd(tmp_path, 'echo \'[{"labels":[]}]\'\n')
    beads = Beads(tmp_path, binary=shim)
    with pytest.raises(RuntimeError):
        beads.show("any-id")


@pytest.mark.parametrize("payload", ["[]", "null", '"x"', "1"])
def test_recall_raises_value_error_for_non_object_payload(tmp_path: Path, payload: str) -> None:
    """bd recall --json normally prints an object; a non-object payload ([], null,
    a string, a number) must raise ValueError, not AttributeError (hel-f8w item 2).
    """
    shim = _write_shim_bd(tmp_path, f"echo '{payload}'\n")
    beads = Beads(tmp_path, binary=shim)
    with pytest.raises(ValueError, match="unexpected payload"):
        beads.recall("any-key")


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_create_round_trips_metadata_types(tmp_path: Path) -> None:
    _init_bd_repo(tmp_path)
    beads = Beads(tmp_path)
    bead_id = beads.create(
        "probe",
        labels=["kind:impl", "unit:x"],
        metadata={"s": "true", "n": None, "flag": True, "files": ["a.py"]},
    )
    bead = beads.show(bead_id)
    assert bead.labels == ["kind:impl", "unit:x"]
    assert bead.metadata["s"] == "true"
    assert bead.metadata["n"] is None
    assert bead.metadata["flag"] is True
    assert bead.files == ["a.py"]


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_non_string_metadata_scalars_read_back_as_json_text(tmp_path: Path) -> None:
    _init_bd_repo(tmp_path)
    beads = Beads(tmp_path)

    bool_id = beads.create("bool test", labels=[], metadata={})
    subprocess.run(
        ["bd", "update", bool_id, "--set-metadata", "test=true"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    assert beads.show(bool_id).test == "true"

    number_id = beads.create("number test", labels=[], metadata={})
    subprocess.run(
        ["bd", "update", number_id, "--set-metadata", "test=3"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    assert beads.show(number_id).test == "3"

    string_id = beads.create("string test", labels=[], metadata={"test": "true"})
    assert beads.show(string_id).test == "true"


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_dep_add_is_idempotent(tmp_path: Path) -> None:
    _init_bd_repo(tmp_path)
    beads = Beads(tmp_path)
    a = beads.create("A", labels=[], metadata={})
    b = beads.create("B", labels=[], metadata={})
    beads.dep_add(a, b)
    beads.dep_add(a, b)  # must not raise


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_list_filters_by_labels_and_status(tmp_path: Path) -> None:
    _init_bd_repo(tmp_path)
    beads = Beads(tmp_path)
    open_id = beads.create("open one", labels=["unit:x"], metadata={})
    closed_id = beads.create("closed one", labels=["unit:x"], metadata={})
    beads.close(closed_id, "done")
    other_id = beads.create("other unit", labels=["unit:y"], metadata={})

    all_x = {b.id for b in beads.list(labels=["unit:x"])}
    assert all_x == {open_id, closed_id}
    assert other_id not in all_x

    open_only = {b.id for b in beads.list(labels=["unit:x"], status="open")}
    assert open_only == {open_id}


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_ready_filters_by_two_labels_with_no_limit(tmp_path: Path) -> None:
    _init_bd_repo(tmp_path)
    beads = Beads(tmp_path)
    both = beads.create("both labels", labels=["foo", "bar"], metadata={})
    one = beads.create("one label", labels=["foo"], metadata={})
    for i in range(150):
        beads.create(f"filler {i}", labels=["foo", "bar"], metadata={})

    ready_ids = {b.id for b in beads.ready(labels=["foo", "bar"])}
    assert both in ready_ids
    assert one not in ready_ids
    assert len(ready_ids) == 151  # not truncated by bd's default -n 100


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_add_label_does_not_raise_on_existing_label(tmp_path: Path) -> None:
    _init_bd_repo(tmp_path)
    beads = Beads(tmp_path)
    bead_id = beads.create("probe", labels=[], metadata={})
    beads.add_label(bead_id, "unit:x")
    beads.add_label(bead_id, "unit:x")  # must not raise
    assert "unit:x" in beads.show(bead_id).labels


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_set_status_moves_out_of_ready(tmp_path: Path) -> None:
    """`set_status` (SPEC §11, hel-dgl item 9) via `bd update --status`."""
    _init_bd_repo(tmp_path)
    beads = Beads(tmp_path)
    bead_id = beads.create("probe", labels=[], metadata={})
    assert beads.show(bead_id).status == "open"
    assert any(b.id == bead_id for b in beads.ready())
    beads.set_status(bead_id, "in_progress")
    assert beads.show(bead_id).status == "in_progress"
    assert not any(b.id == bead_id for b in beads.ready())


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_gate_list_and_gate_blocks_two_beads(tmp_path: Path) -> None:
    _init_bd_repo(tmp_path)
    beads = Beads(tmp_path)
    a = beads.create("A", labels=[], metadata={})
    b = beads.create("B", labels=[], metadata={})
    gate = subprocess.run(
        ["bd", "gate", "create", "--blocks", a, "--reason", "review", "--json"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    gate_id = json.loads(gate.stdout)["id"]
    beads.dep_add(b, gate_id)

    gates = beads.gate_list()
    assert [g["id"] for g in gates] == [gate_id]
    assert beads.gate_blocks(gate_id) == sorted([a, b])


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_remember_recall_round_trip(tmp_path: Path) -> None:
    _init_bd_repo(tmp_path)
    beads = Beads(tmp_path)
    assert beads.recall("missing") is None
    cases = {
        "no-newline": "hello world",
        "one-newline": "hello world\n",
        "two-newlines": "hello world\n\n",
        "multiline": "line1\nline2\nline3",
        "leading-dash": "-not-a-flag value",
    }
    for key, value in cases.items():
        beads.remember(key, value)
    for key, value in cases.items():
        assert beads.recall(key) == value
    assert beads.memories() == cases


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_memories_keeps_a_key_named_schema_version(tmp_path: Path) -> None:
    _init_bd_repo(tmp_path)
    beads = Beads(tmp_path)
    beads.remember("schema_version", "not bd's own field")
    beads.remember("foo", "bar")
    memories = beads.memories()
    assert memories["foo"] == "bar"
    # bd's own bookkeeping field (an int) is dropped; it is never mistaken for a
    # string-valued memory, whatever key that memory happens to be stored under.
    assert "schema_version" not in memories or isinstance(memories["schema_version"], str)
    assert beads.recall("schema_version") == "not bd's own field"


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_remember_does_not_raise_on_truncated_multibyte_echo(tmp_path: Path) -> None:
    _init_bd_repo(tmp_path)
    beads = Beads(tmp_path)
    value = "a" * 60 + "é" * 20
    beads.remember("multibyte", value)  # must not raise UnicodeDecodeError
    assert beads.recall("multibyte") == value
