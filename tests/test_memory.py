"""Memory backend tests (SPEC §13)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from helios import memory as mem
from helios.beads import Bead, BeadNotFound, Beads, FakeBeads
from helios.config import Config, MemoryConfig, load

KEY = "probe"
H = {"source": "hel-a02#1"}


def files_backend(tmp_path: Path) -> mem.FilesBackend:
    return mem.FilesBackend(tmp_path / "store")


def sample_header(**extra: Any) -> dict[str, Any]:
    header: dict[str, Any] = {"source": "hel-a02#1"}
    header.update(extra)
    return header


def canon(header: dict[str, Any], body: str) -> bytes:
    return mem.serialize(header, body).encode("utf-8")


# 1) Format: serialize and parse are exact inverses.


@settings(max_examples=200)
@given(
    header=st.dictionaries(st.text(), st.text(), max_size=4),
    body=st.text(alphabet=st.characters(codec=None, blacklist_categories=())),
)
def test_format_inverse_any_text(header: dict[str, Any], body: str) -> None:
    full = {**header, "status": "active"}
    text = mem.serialize(full, body)
    parsed = mem.parse("k", text)
    assert (parsed.header, parsed.body) == (full, body)
    assert mem.serialize(parsed.header, parsed.body) == text


@pytest.mark.parametrize(
    "body",
    ["", "x", "x\n", "x\n\n", "a\r\n", "a\r", "\u2028", "\x00",
     "helios-memory 1\n{}\n\n", "y" * 1_000_000],
)
def test_format_inverse_edges(body: str) -> None:
    parsed = mem.parse("k", mem.serialize({"source": "s", "status": "active"}, body))
    assert parsed.body == body


def test_nonfinite_header_refused() -> None:
    with pytest.raises(ValueError):
        mem.serialize({"source": "s", "n": float("nan")}, "b")
    with pytest.raises(ValueError):
        mem.serialize({"source": "s", "i": float("inf")}, "b")
    for raw in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValueError) as excinfo:
            mem.parse("nan-key", f'helios-memory 1\n{{"n": {raw}, "source": "s"}}\n\nb')
        assert "nan-key" in str(excinfo.value)


def test_format_line2_is_sorted_json_with_default_separators() -> None:
    text = mem.serialize({"source": "s", "unit": "U", "commit": "3f1e9a"}, "b")
    lines = text.split("\n")
    assert lines[0] == "helios-memory 1"
    assert lines[1] == json.dumps(
        {"commit": "3f1e9a", "source": "s", "unit": "U"},
        sort_keys=True,
        ensure_ascii=False,
    )
    assert lines[2] == ""
    assert "\n".join(lines[3:]) == "b"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "helios-memory 1",
        "helios-memory 1\n",
        "helios-memory 2\n{}\n\nbody",
        "helios-memory 1\r\n{}\n\nb",
        "helios-memory 1 \n{}\n\nb",
        "Helios-memory 1\n{}\n\nb",
        "helios-memory 1\n{}\r\n\nb",
        "helios-memory 1\n{} \n\nb",
        'helios-memory 1\n{"a":1}\n\nb',
        'helios-memory 1\n{"source": "s"}\n\nb',
        'helios-memory 1\n{"b": 1, "a": 2}\n\nb',
        'helios-memory 1\n{"a": 1, "a": 2}\n\nb',
        'helios-memory 1\n{"a": "\\u00e9"}\n\nb',
        "helios-memory 1\n{}\n",
        "helios-memory 1\n{}\nbody",
        "helios-memory 1\n{bad}\n\nbody",
        "helios-memory 1\n[1, 2]\n\nbody",
        'helios-memory 1\n"str"\n\nbody',
        "helios-memory 1\n42\n\nbody",
        "helios-memory 1\nnull\n\nbody",
        "helios-memory 1\n\n\nbody",
        "x\nhelios-memory 1\n{}\n\nbody",
    ],
)
def test_format_malformed_prefix_raises_naming_key(text: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        mem.parse("my-key", text)
    assert "my-key" in str(excinfo.value)


# 2) Key rule and every write refusal.


@pytest.mark.parametrize("key", ["a", "a0", "k.e-y_z", "0abc", "a.", "a..", "a.md",
                                 "a..b", "0", "a_b-c.d", "schema-version"])
def test_key_rule_accepts(tmp_path: Path, key: str) -> None:
    backend = files_backend(tmp_path)
    backend.write(key, sample_header(), "body")
    assert backend.read(key).body == "body"


def test_key_rule_length_boundary(tmp_path: Path) -> None:
    backend = files_backend(tmp_path)
    backend.write("x" * 200, sample_header(), "body")
    assert backend.read("x" * 200).body == "body"
    out = tmp_path / "out"
    backend.export(out)
    assert (out / ("x" * 200 + ".md")).is_file()
    with pytest.raises(ValueError) as excinfo:
        backend.write("x" * 201, sample_header(), "body")
    assert "x" * 201 in str(excinfo.value)


@pytest.mark.parametrize(
    "key",
    ["a\n", "abc\n", "Key", "A", ".", "..", "a/b", "a\\b", "", " a", "a ",
     "é", "ａ", "٣", "-a", ".a", "_a", "a*", "x" * 201, "schema_version"],
)
def test_key_rule_rejects(tmp_path: Path, key: str) -> None:
    backend = files_backend(tmp_path)
    with pytest.raises(ValueError) as excinfo:
        backend.write(key, sample_header(), "body")
    assert repr(key) in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        backend.read(key)
    assert repr(key) in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        backend.inject([key])
    assert repr(key) in str(excinfo.value)


def test_key_trailing_newline_rejected_beads(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        mem.BeadsBackend(FakeBeads(), tmp_path / "e").write("a\n", H, "b")


@pytest.mark.parametrize("header", [{}, {"source": ""}, {"source": 3}, {"source": None}])
def test_write_refuses_bad_source(tmp_path: Path, header: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="source"):
        files_backend(tmp_path).write(KEY, header, "body")


def test_write_refuses_bad_status(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bogus"):
        files_backend(tmp_path).write(KEY, sample_header(status="bogus"), "body")


@pytest.mark.parametrize(
    "header,exc",
    [
        ({"source": " "}, None),
        ({"source": ["s"]}, ValueError),
        ({"source": "s", "status": None}, ValueError),
        ({"source": "s", "status": "Active"}, ValueError),
        ({"source": "s", "status": ["active"]}, ValueError),
        ({"source": "s", "status": "superseded"}, None),
        ({"source": "s", "status": "retracted"}, None),
    ],
)
def test_write_refusals(tmp_path: Path, header: dict[str, Any], exc: type | None) -> None:
    backend = files_backend(tmp_path)
    if exc is None:
        backend.write("k", header, "b")
        assert backend.read("k").header["status"] == header.get("status", "active")
    else:
        with pytest.raises(exc):
            backend.write("k", header, "b")
        assert not (tmp_path / "store" / "k.md").exists()


def test_write_refusal_does_not_call_bd(tmp_path: Path) -> None:
    fake = FakeBeads()
    backend = mem.BeadsBackend(fake, tmp_path / "e")
    for header in ({}, {"source": "s", "status": "x"}):
        with pytest.raises(ValueError):
            backend.write("k", header, "b")
    assert fake.argv_log == []


def test_write_defaults_missing_status_to_active(tmp_path: Path) -> None:
    backend = files_backend(tmp_path)
    backend.write(KEY, {"source": "s#1", "unit": "U14"}, "body")
    stored = backend.read(KEY)
    assert stored.header["status"] == "active"
    assert stored.header["unit"] == "U14"


@pytest.mark.parametrize("value", ["", "0", "hel-1"])
def test_helios_bead_any_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("HELIOS_BEAD", value)
    fake = FakeBeads()
    with pytest.raises(PermissionError):
        mem.BeadsBackend(fake, tmp_path / "e").write("k", H, "b")
    with pytest.raises(PermissionError):
        files_backend(tmp_path).write("k", {}, "b")
    with pytest.raises(PermissionError):
        files_backend(tmp_path).import_(tmp_path)
    assert fake.argv_log == []


def test_header_not_mutated_other_keys_kept(tmp_path: Path) -> None:
    backend = files_backend(tmp_path)
    header = {"source": "s", "unit": "U", "nested": {"z": [1, {"y": None}]}, "é": "ü"}
    snapshot = json.dumps(header, sort_keys=True)
    backend.write("k", header, "b")
    assert json.dumps(header, sort_keys=True) == snapshot and "status" not in header
    assert backend.read("k").header == {**header, "status": "active"}


def test_supersedes_never_edits(tmp_path: Path) -> None:
    backend = files_backend(tmp_path)
    backend.write("old", H, "old body")
    before = (tmp_path / "store" / "old.md").read_bytes()
    backend.write("new", {"source": "s", "supersedes": "old"}, "new")
    assert (tmp_path / "store" / "old.md").read_bytes() == before
    fake = FakeBeads()
    beads_backend = mem.BeadsBackend(fake, tmp_path / "e")
    beads_backend.write("old", H, "old body")
    stored = fake.recall("old")
    beads_backend.write("new", {"source": "s", "supersedes": "old", "status": "active"}, "n")
    assert fake.recall("old") == stored
    assert [a[2] for a in fake.argv_log if a[0] == "remember"] == ["old", "new"]


def test_surrogate_header_raises_naming_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as excinfo:
        files_backend(tmp_path).write("surr-key", {"source": "s", "x": "\ud800"}, "b")
    assert "surr-key" in str(excinfo.value)


def test_body_size_limit(tmp_path: Path) -> None:
    backend = files_backend(tmp_path)
    backend.write("ok", H, "y" * 60000)
    assert len(backend.read("ok").body.encode("utf-8")) == 60000
    fake = FakeBeads()
    beads_backend = mem.BeadsBackend(fake, tmp_path / "e")
    with pytest.raises(ValueError) as excinfo:
        backend.write("big", H, "y" * 60001)
    assert "big" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        beads_backend.write("big", H, "y" * 60001)
    assert "big" in str(excinfo.value)
    assert fake.recall("big") is None
    assert not (tmp_path / "store" / "big.md").exists()
    assert not (tmp_path / "e" / "big.md").exists()


# hel-j31 item 2: a header value json cannot serialize (TypeError from
# json.dumps) raises ValueError naming the key, on every backend.


class _Unserializable:
    pass


@pytest.mark.parametrize("bad_value", [{1, 2}, b"bytes", _Unserializable()])
def test_header_unserializable_value_raises_valueerror(tmp_path: Path, bad_value: Any) -> None:
    fake = FakeBeads()
    for backend in (files_backend(tmp_path), mem.BeadsBackend(fake, tmp_path / "e")):
        with pytest.raises(ValueError) as excinfo:
            backend.write("bad-ser", {"source": "s", "x": bad_value}, "b")
        assert "bad-ser" in str(excinfo.value)
    assert fake.argv_log == []
    assert not (tmp_path / "store").exists() and not (tmp_path / "e").exists()


# hel-j31 item 3: a non-str dict key anywhere in the header is refused
# instead of silently stringified, on every backend.


@pytest.mark.parametrize(
    "header",
    [
        {"source": "s", "n": {1: "a"}},
        {"source": "s", "n": {"a": 1, 2: "b"}},
        {"source": "s", "n": {(1, 2): "v"}},
    ],
)
def test_header_nonstr_dict_key_refused(tmp_path: Path, header: dict[str, Any]) -> None:
    fake = FakeBeads()
    for backend in (files_backend(tmp_path), mem.BeadsBackend(fake, tmp_path / "e")):
        with pytest.raises(ValueError) as excinfo:
            backend.write("nsk", header, "b")
        assert "nsk" in str(excinfo.value)
    assert fake.argv_log == []
    assert not (tmp_path / "store").exists() and not (tmp_path / "e").exists()


def test_nul_in_body_or_header_raises_naming_key(tmp_path: Path) -> None:
    backend = files_backend(tmp_path)
    with pytest.raises(ValueError) as excinfo:
        backend.write("nul-body", H, "a\x00b")
    assert "nul-body" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        backend.write("nul-head", {"source": "s\x00"}, "b")
    assert "nul-head" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        backend.write("nul-nest", {"source": "s", "n": ["\x00"]}, "b")
    assert "nul-nest" in str(excinfo.value)


# 3) files backend read, write, export, import_.


def test_files_backend_exact_bytes_and_missing_key(tmp_path: Path) -> None:
    backend = files_backend(tmp_path)
    header = sample_header(status="active", unit="U14")
    backend.write("m1", header, "line1\nline2\n")
    path = tmp_path / "store" / "m1.md"
    assert path.read_bytes() == canon(header, "line1\nline2\n")
    assert backend.read("m1") == mem.Memory(key="m1", header={**header}, body="line1\nline2\n")
    with pytest.raises(KeyError):
        backend.read("nope")


def test_files_export_import_round_trip_byte_for_byte(tmp_path: Path) -> None:
    backend = files_backend(tmp_path)
    backend.write("a", sample_header(status="active"), "no newline")
    backend.write("b", sample_header(status="retracted"), "one\n")
    backend.write("c", {"source": "hel-9#3", "note": "héllo\r\n"}, "")
    dir_a = tmp_path / "a"
    backend.export(dir_a)
    assert sorted(p.name for p in dir_a.iterdir()) == ["a.md", "b.md", "c.md"]
    (dir_a / "extra.md").write_bytes(canon({"source": "x#1", "status": "active"}, "kept"))
    backend.export(dir_a)  # never deletes files
    assert (dir_a / "extra.md").is_file()

    fresh = mem.FilesBackend(tmp_path / "fresh")
    fresh.import_(dir_a)
    assert fresh.read("extra").body == "kept"
    dir_b = tmp_path / "b"
    fresh.export(dir_b)
    assert sorted(p.name for p in dir_b.iterdir()) == sorted(p.name for p in dir_a.iterdir())
    for path in dir_a.iterdir():
        assert (dir_b / path.name).read_bytes() == path.read_bytes()


def test_files_tree_round_trip_bounded(tmp_path: Path) -> None:
    clean = st.text(alphabet=st.characters(codec="utf-8", exclude_categories=("Cc",)), max_size=50)
    clean_key = clean.filter(lambda k: k != "status")

    @settings(max_examples=30)
    @given(
        items=st.dictionaries(
            st.from_regex(r"\A[a-z0-9][a-z0-9._-]{0,10}\Z"),
            st.tuples(
                st.dictionaries(clean_key, clean, max_size=3),
                clean,
            ),
            max_size=5,
        )
    )
    def check(items: dict[str, tuple[dict[str, str], str]]) -> None:
        root = tmp_path / f"rt-{len(list(tmp_path.iterdir()))}"
        backend = mem.FilesBackend(root / "store")
        for key, (header, body) in items.items():
            backend.write(key, {**header, "source": "s#1"}, body)
        dir_a = root / "a"
        backend.export(dir_a)
        fresh = mem.FilesBackend(root / "fresh")
        fresh.import_(dir_a)
        dir_b = root / "b"
        fresh.export(dir_b)
        assert {p.name: p.read_bytes() for p in dir_a.iterdir()} == {
            p.name: p.read_bytes() for p in dir_b.iterdir()
        }
        for key, (_header, body) in items.items():
            assert fresh.read(key).body == body

    check()


def test_import_canonical_file_is_byte_for_byte(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    raw = canon({"source": "s", "status": "active"}, "b")
    (src / "k.md").write_bytes(raw)
    backend = files_backend(tmp_path)
    backend.import_(src)
    out = tmp_path / "out"
    backend.export(out)
    assert (out / "k.md").read_bytes() == raw


@pytest.mark.parametrize(
    "raw",
    [
        b'helios-memory 1\n{"source":"s","status":"active"}\n\nb',
        b'helios-memory 1\n{"status": "active", "source": "s"}\n\nb',
        b'helios-memory 1\n{"source": "\\u00e9", "status": "active"}\n\nb',
        b'helios-memory 1\n{"source": "s", "status": "active"}\r\n\nb',
    ],
)
def test_import_rejects_noncanonical(tmp_path: Path, raw: bytes) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "k.md").write_bytes(raw)
    backend = files_backend(tmp_path)
    with pytest.raises(ValueError) as excinfo:
        backend.import_(src)
    assert "k" in str(excinfo.value)
    store = tmp_path / "store"
    assert not store.exists() or list(store.glob("*.md")) == []


def test_import_order_and_scope(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    for name in ["b", "a", "a.b", "a-b"]:
        (src / f"{name}.md").write_bytes(canon({"source": "s", "status": "active"}, name))
    (src / "x.MD").write_bytes(canon({"source": "s"}, "x"))
    (src / "y.md.txt").write_bytes(b"junk")
    order: list[str] = []

    class Rec(mem.FilesBackend):
        def write(self, key: str, header: dict[str, Any], body: str) -> None:
            order.append(key)
            super().write(key, header, body)

    Rec(tmp_path / "store").import_(src)
    assert order == ["a", "a-b", "a.b", "b"]


# Round 3: import_ checks every entry ending in .md, dotfiles included; a bad
# name or a non-regular-file entry (a broken symlink, a directory named
# x.md) raises ValueError naming it, and nothing is written (decided).


@pytest.mark.parametrize(
    "kind,bad_name",
    [("hidden", ".hidden"), ("broken-symlink", "c"), ("dir", "dir")],
)
def test_import_hidden_broken_and_dir_md_checked(
    tmp_path: Path, kind: str, bad_name: str
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_bytes(canon({"source": "s", "status": "active"}, "a"))
    (src / "b.md").write_bytes(canon({"source": "s", "status": "active"}, "b"))
    if kind == "hidden":
        (src / ".hidden.md").write_bytes(b"junk")
    elif kind == "broken-symlink":
        (src / "c.md").symlink_to(tmp_path / "nowhere.md")
    else:
        (src / "dir.md").mkdir()
    fake = FakeBeads()
    for backend, root in (
        (mem.FilesBackend(tmp_path / f"store-{kind}"), tmp_path / f"store-{kind}"),
        (mem.BeadsBackend(fake, tmp_path / f"e-{kind}"), tmp_path / f"e-{kind}"),
    ):
        with pytest.raises(ValueError) as excinfo:
            backend.import_(src)
        assert bad_name in str(excinfo.value)
        assert not root.exists() or list(root.glob("*.md")) == []
    assert fake.argv_log == []


# hel-j31 item 4: export and stale use the same entry listing and
# regular-file check as import_, instead of silently skipping a dotfile or a
# non-regular x.md entry ahead of time.


@pytest.mark.parametrize(
    "kind,bad_name",
    [("hidden", ".hidden"), ("broken-symlink", "c"), ("dir", "dir")],
)
def test_export_hidden_broken_and_dir_md_checked(
    tmp_path: Path, kind: str, bad_name: str
) -> None:
    store = tmp_path / f"store-{kind}"
    store.mkdir()
    (store / "a.md").write_bytes(canon({"source": "s", "status": "active"}, "a"))
    (store / "b.md").write_bytes(canon({"source": "s", "status": "active"}, "b"))
    if kind == "hidden":
        (store / ".hidden.md").write_bytes(b"junk")
    elif kind == "broken-symlink":
        (store / "c.md").symlink_to(tmp_path / "nowhere.md")
    else:
        (store / "dir.md").mkdir()
    backend = mem.FilesBackend(store)
    out = tmp_path / f"out-{kind}"
    with pytest.raises(ValueError) as excinfo:
        backend.export(out)
    assert bad_name in str(excinfo.value)
    assert not out.exists()


@pytest.mark.parametrize(
    "kind,bad_name",
    [("hidden", ".hidden"), ("broken-symlink", "c"), ("dir", "dir")],
)
def test_stale_hidden_broken_and_dir_md_skipped_with_note(
    tmp_path: Path, kind: str, bad_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / f"store-{kind}"
    store.mkdir()
    backend = mem.FilesBackend(
        store, labels_of=lambda bead_id: {"b1": ["truth:wrong"]}.get(bead_id)
    )
    backend.write("m1", {"source": "b1#1"}, "x")
    backend.write("m2", {"source": "b2#1"}, "x")
    if kind == "hidden":
        (store / ".hidden.md").write_bytes(b"junk")
    elif kind == "broken-symlink":
        (store / "c.md").symlink_to(tmp_path / "nowhere.md")
    else:
        (store / "dir.md").mkdir()
    assert backend.stale() == ["m1"]
    err = capsys.readouterr().err
    assert bad_name in err


def test_import_bad_file_writes_nothing(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_bytes(canon({"source": "s", "status": "active"}, "a"))
    (src / "z.md").write_bytes(canon({"source": "s", "status": "active"}, "z"))
    (src / "_draft.md").write_bytes(b"helios-memory 1\n{}\n\n")
    backend = files_backend(tmp_path)
    with pytest.raises(ValueError) as excinfo:
        backend.import_(src)
    assert "_draft" in str(excinfo.value)
    store = tmp_path / "store"
    assert not store.exists() or list(store.glob("*.md")) == []


def test_import_non_utf8_names_key(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "bin.md").write_bytes(b"helios-memory 1\n{}\n\n\xff")
    with pytest.raises(ValueError, match="bin"):
        files_backend(tmp_path).import_(src)


def test_import_ignores_subdirs_and_non_md(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.md").write_bytes(canon({"source": "s#1", "status": "active"}, "ok"))
    (src / "notes.txt").write_text("not a memory")
    sub = src / "sub"
    sub.mkdir()
    (sub / "inner.md").write_bytes(canon({"source": "s#1", "status": "active"}, "i"))
    backend = files_backend(tmp_path)
    backend.import_(src)
    assert backend.read("ok").body == "ok"
    with pytest.raises(KeyError):
        backend.read("inner")
    with pytest.raises(KeyError):
        backend.read("notes")


# Round 3: the files backend finds a key's file by exact name, never by a
# path the filesystem may match case-insensitively (decided).


def test_files_backend_reads_by_exact_case(tmp_path: Path) -> None:
    probe = tmp_path / "CaseProbe.tmp"
    probe.write_text("x")
    if not (tmp_path / "caseprobe.tmp").exists():
        pytest.skip("filesystem is case-sensitive")
    store = tmp_path / "s"
    store.mkdir()
    (store / "A.md").write_bytes(canon({"source": "s", "status": "active"}, "upper"))
    fb = mem.FilesBackend(store)
    # A.md is not an exact match for key "a": read must not find it through
    # the filesystem's case fold.
    with pytest.raises(KeyError):
        fb.read("a")
    # A fresh key with no colliding name writes and reads back exactly.
    store2 = tmp_path / "s2"
    store2.mkdir()
    fb2 = mem.FilesBackend(store2)
    fb2.write("a", {"source": "s"}, "fresh")
    assert os.listdir(store2) == ["a.md"]
    fb2.write("a", {"source": "s"}, "updated")
    assert fb2.read("a").body == "updated"


# hel-j31 item 1: write beside a differently-cased file refuses instead of
# silently keeping the old name.


def test_write_beside_differently_cased_file_raises(tmp_path: Path) -> None:
    probe = tmp_path / "CaseProbe.tmp"
    probe.write_text("x")
    if not (tmp_path / "caseprobe.tmp").exists():
        pytest.skip("filesystem is case-sensitive")
    store = tmp_path / "s"
    store.mkdir()
    (store / "A.md").write_bytes(canon({"source": "s", "status": "active"}, "upper"))
    fb = mem.FilesBackend(store)
    with pytest.raises(ValueError) as excinfo:
        fb.write("a", H, "lower")
    assert "'a'" in str(excinfo.value)
    assert "A.md" in str(excinfo.value)
    assert (store / "A.md").read_bytes() == canon({"source": "s", "status": "active"}, "upper")
    assert os.listdir(store) == ["A.md"]
    with pytest.raises(KeyError):
        fb.read("a")


def test_export_symlink_target_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"precious")
    backend = files_backend(tmp_path)
    backend.write("k", H, "b")
    out = tmp_path / "out"
    out.mkdir()
    (out / "k.md").symlink_to(outside)
    backend.export(out)
    assert outside.read_bytes() == b"precious"
    assert not (out / "k.md").is_symlink()


def test_export_never_deletes_and_leaves_no_tmp(tmp_path: Path) -> None:
    backend = files_backend(tmp_path)
    backend.write("k", H, "b")
    out = tmp_path / "out"
    out.mkdir()
    (out / "other.md").write_bytes(b"x")
    (out / "notes.txt").write_bytes(b"y")
    (out / "sub").mkdir()
    backend.export(out)
    assert sorted(p.name for p in out.iterdir()) == ["k.md", "notes.txt", "other.md", "sub"]


def test_files_export_malformed_raises_naming_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "store"
    store.mkdir()
    (store / "bad.md").write_bytes(b"not a memory")
    backend = mem.FilesBackend(store)
    with pytest.raises(ValueError) as excinfo:
        backend.export(tmp_path / "out")
    assert "bad" in str(excinfo.value)


def test_files_export_into_itself(tmp_path: Path) -> None:
    backend = files_backend(tmp_path)
    backend.write("k", H, "b\n")
    before = (tmp_path / "store" / "k.md").read_bytes()
    backend.export(tmp_path / "store")
    assert (tmp_path / "store" / "k.md").read_bytes() == before


def test_concurrent_writes_same_key(tmp_path: Path) -> None:
    backend = files_backend(tmp_path)
    bodies = [f"body-{i}\n" * 1000 for i in range(16)]
    threads = [threading.Thread(target=backend.write, args=("k", H, body)) for body in bodies]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert backend.read("k").body in bodies
    assert [p.name for p in (tmp_path / "store").iterdir()] == ["k.md"]


# 4) beads backend with FakeBeads.


def test_beads_write_calls_bd_before_export_file(tmp_path: Path) -> None:
    export_dir = tmp_path / "exp"

    class FailingBeads(FakeBeads):
        def remember(self, key: str, value: str) -> None:
            raise RuntimeError("bd is down")

    with pytest.raises(RuntimeError, match="bd is down"):
        mem.BeadsBackend(FailingBeads(), export_dir).write(KEY, H, "b")
    assert not (export_dir / f"{KEY}.md").exists()

    fake = FakeBeads()
    backend = mem.BeadsBackend(fake, export_dir)
    backend.write(KEY, H, "b")
    stored = fake.recall(KEY)
    assert stored == canon({"source": "hel-a02#1", "status": "active"}, "b").decode()
    assert stored is not None
    assert (export_dir / f"{KEY}.md").read_bytes() == stored.encode()


def test_beads_order_bd_then_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    fake = FakeBeads()
    orig_remember = fake.remember
    fake.remember = lambda k, v: (events.append("bd"), orig_remember(k, v))  # type: ignore[method-assign]
    orig_replace = os.replace
    orig_mkstemp = mem.tempfile.mkstemp

    def rep(a: Any, b: Any) -> None:
        events.append(f"replace:{Path(b).name}")
        orig_replace(a, b)

    def mk(*a: Any, **kw: Any) -> Any:
        events.append("mkstemp")
        return orig_mkstemp(*a, **kw)

    monkeypatch.setattr(mem.os, "replace", rep)
    monkeypatch.setattr(mem.tempfile, "mkstemp", mk)
    mem.BeadsBackend(fake, tmp_path / "e").write("k", H, "b")
    assert events == ["bd", "mkstemp", "replace:k.md"]


def test_crash_between_bd_and_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeBeads()
    backend = mem.BeadsBackend(fake, tmp_path / "e")
    backend.write("k", H, "v1")

    def boom(*a: Any, **kw: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(mem, "_write_bytes_atomic", boom)
    with pytest.raises(OSError):
        backend.write("k", H, "v2")
    monkeypatch.undo()
    assert backend.read("k").body == "v2"
    assert (tmp_path / "e" / "k.md").read_bytes() != canon(
        {**H, "status": "active"}, "v2"
    )
    backend.export(tmp_path / "e")  # healing export into export_dir
    assert (tmp_path / "e" / "k.md").read_bytes() == canon({**H, "status": "active"}, "v2")
    assert [p.name for p in (tmp_path / "e").iterdir()] == ["k.md"]


def test_beads_export_skips_non_memory_values_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeBeads()
    backend = mem.BeadsBackend(fake, tmp_path / "e")
    backend.write("good", H, "body")
    fake.remember("junk", "plain")
    fake.remember("crlfmagic", "helios-memory 1\r\n{}\n\nb")
    fake.remember("malformed", "helios-memory 1\n{bad\n\nb")
    fake.remember("bad key", mem.serialize({"source": "s"}, "b"))
    out = tmp_path / "out"
    backend.export(out)
    assert [p.name for p in out.iterdir()] == ["good.md"]
    err = capsys.readouterr().err
    for key in ("junk", "crlfmagic", "malformed", "bad key"):
        assert key in err
    backend.stale()
    assert "junk" in capsys.readouterr().err


def test_schema_version_key_refused_everywhere(tmp_path: Path) -> None:
    """bd hides schema_version from bd memories, so the key rule refuses it."""
    fake = FakeBeads()
    for backend in (files_backend(tmp_path), mem.BeadsBackend(fake, tmp_path / "e")):
        with pytest.raises(ValueError) as excinfo:
            backend.write("schema_version", H, "b")
        assert "schema_version" in str(excinfo.value)
        with pytest.raises(ValueError):
            backend.read("schema_version")
    assert fake.argv_log == []
    assert not (tmp_path / "store").exists() and not (tmp_path / "e").exists()


BD = shutil.which("bd")


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_beads_real_bd_round_trip_trailing_newlines(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["bd", "init", "--non-interactive"], cwd=tmp_path, check=True, capture_output=True
    )
    backend = mem.BeadsBackend(Beads(tmp_path), tmp_path / "exp")
    bodies = ["text", "text\n", "text\n\n"]
    for i, body in enumerate(bodies):
        backend.write(f"k{i}", H, body)
    for i, body in enumerate(bodies):
        assert backend.read(f"k{i}").body == body


# 5) stale with an injected label lookup.


def stale_backend(tmp_path: Path) -> mem.FilesBackend:
    backend = mem.FilesBackend(
        tmp_path / "store",
        labels_of=lambda bead_id: {
            "b1": ["truth:wrong"],
            "b2": [],
        }.get(bead_id),
    )
    backend.write("m1", {"source": "b1#1"}, "x")
    backend.write("m0", {"source": "b1#7"}, "x")
    backend.write("m2", {"source": "b2"}, "x")
    backend.write("m3", {"source": "missing#2"}, "x")
    backend.write("m4", {"source": "b1#1", "status": "retracted"}, "x")
    backend.write("m5", {"source": "b1", "status": "superseded"}, "x")
    return backend


def test_stale_sorted_missing_bead_not_stale(tmp_path: Path) -> None:
    assert stale_backend(tmp_path).stale() == ["m0", "m1"]


def test_stale_source_id_and_label_edges(tmp_path: Path) -> None:
    seen: list[str] = []

    def labels(bead_id: str) -> list[str] | None:
        seen.append(bead_id)
        return {
            "hel-1": ["truth:wrong"],
            "": ["truth:wrong"],
            "hel-2": ["truth:wrong:x", "Truth:wrong"],
        }.get(bead_id)

    backend = mem.FilesBackend(tmp_path / "s", labels_of=labels)
    backend.write("z1", {"source": "hel-1"}, "x")
    backend.write("a2", {"source": "hel-1#2#3"}, "x")
    backend.write("m3", {"source": "#3"}, "x")
    backend.write("b4", {"source": "hel-2#1"}, "x")
    backend.write("c5", {"source": "hel-1#1", "status": "superseded"}, "x")
    backend.write("c6", {"source": "hel-1#1"}, "x")
    assert backend.stale() == ["a2", "c6", "z1"]
    assert "hel-1" in seen and "hel-1#2" not in seen


def test_stale_beads_via_fake_show(tmp_path: Path) -> None:
    fake = FakeBeads([Bead(id="hel-1", labels=["truth:wrong"]), Bead(id="hel-2", labels=[])])
    backend = mem.BeadsBackend(fake, tmp_path / "e")
    backend.write("x", {"source": "hel-1#1"}, "b")
    backend.write("y", {"source": "hel-2#1"}, "b")
    backend.write("w", {"source": "hel-404#1"}, "b")
    assert backend.stale() == ["x"]


def test_stale_partial_id_is_not_stale(tmp_path: Path) -> None:
    class PartialBeads(FakeBeads):
        def show(self, bead_id: str) -> Bead:
            if bead_id == "3f":
                return Bead(id="helbd2_RrY7-3fd", labels=["truth:wrong"])
            raise KeyError(bead_id)

    backend = mem.BeadsBackend(PartialBeads(), tmp_path / "e")
    backend.write("p", {"source": "3f#1"}, "b")
    assert backend.stale() == []


def test_stale_lookup_failure_propagates(tmp_path: Path) -> None:
    def labels(bead_id: str) -> list[str] | None:
        raise RuntimeError("bd not installed")

    backend = mem.FilesBackend(tmp_path / "s", labels_of=labels)
    backend.write("x", {"source": "hel-1#1"}, "b")
    with pytest.raises(RuntimeError):
        backend.stale()


# 6) inject exact bytes and KeyError.


def test_inject_exact_bytes_and_missing_key(tmp_path: Path) -> None:
    for backend in (
        files_backend(tmp_path),
        mem.BeadsBackend(FakeBeads(), tmp_path / "e"),
    ):
        backend.write("a", H, "A\r\n")
        backend.write("b", H, "")
        assert backend.inject([]) == ""
        assert backend.inject(["a"]) == "### a\n\nA\r\n"
        assert backend.inject(["b", "a", "b"]) == "### b\n\n\n\n### a\n\nA\r\n\n\n### b\n\n"
        with pytest.raises(KeyError):
            backend.inject(["a", "zz"])
        assert backend.inject(("a",)) == "### a\n\nA\r\n"


# 7) open_backend.


def test_open_backend(tmp_path: Path) -> None:
    assert isinstance(
        mem.open_backend(Config(hub=tmp_path, memory=MemoryConfig(backend="beads"))),
        mem.BeadsBackend,
    )
    assert isinstance(
        mem.open_backend(Config(hub=tmp_path, memory=MemoryConfig(backend="files"))),
        mem.FilesBackend,
    )
    with pytest.raises(ValueError, match="unknown memory backend 'zzz'"):
        mem.open_backend(Config(hub=tmp_path, memory=MemoryConfig(backend="zzz")))


def test_open_backend_loaded_config(tmp_path: Path) -> None:
    (tmp_path / ".agents").mkdir()
    for value, cls in (("beads", mem.BeadsBackend), ("files", mem.FilesBackend)):
        (tmp_path / ".agents" / "workflow.toml").write_text(
            f'[memory]\nbackend = "{value}"\nexport_dir = "mem/exp"\n'
        )
        cfg = load(tmp_path)
        backend = mem.open_backend(cfg)
        assert isinstance(backend, cls)
        directory = (
            backend.export_dir if isinstance(backend, mem.BeadsBackend) else backend.directory
        )
        assert directory == cfg.hub / "mem/exp"
    assert isinstance(mem.open_backend(Config(hub=tmp_path)), mem.BeadsBackend)
    for bad in ("Beads", "", " files", "file"):
        with pytest.raises(ValueError) as excinfo:
            mem.open_backend(Config(hub=tmp_path, memory=MemoryConfig(backend=bad)))
        assert str(excinfo.value) == f"unknown memory backend '{bad}'"


# Round 2: keys on both backends, nothing written on refusal.


@pytest.mark.parametrize("key", ["x" * 201, "-a", ".a", "_a", "a\n", "A", "a b", "é", "a/b", ""])
def test_key_rejected_nothing_written(tmp_path: Path, key: str) -> None:
    fake = FakeBeads()
    for backend in (files_backend(tmp_path), mem.BeadsBackend(fake, tmp_path / "e")):
        with pytest.raises(ValueError) as excinfo:
            backend.write(key, H, "b")
        assert repr(key) in str(excinfo.value)
    assert fake.argv_log == []
    assert not (tmp_path / "s").exists() and not (tmp_path / "e").exists()


# Round 2: body limit at multi-byte boundaries.


def _ok_body(ch: str) -> str:
    return "y" * (60000 - len(ch.encode())) + ch


def _over_bodies(ch: str) -> list[str]:
    n = len(ch.encode())
    return ["y" * (60001 - n) + ch, "y" * 59999 + ch]


@pytest.mark.parametrize("ch", ["é", "€", "😀"])
def test_body_limit_files_and_fake(tmp_path: Path, ch: str) -> None:
    fake = FakeBeads()
    backends = (files_backend(tmp_path), mem.BeadsBackend(fake, tmp_path / "e"))
    body = _ok_body(ch)
    assert len(body.encode()) == 60000
    for backend in backends:
        backend.write("ok", H, body)
        assert backend.read("ok").body == body
    for bad in _over_bodies(ch):
        assert len(bad.encode()) > 60000
        for backend in backends:
            with pytest.raises(ValueError) as excinfo:
                backend.write("big-key", H, bad)
            assert "big-key" in str(excinfo.value)
    assert fake.recall("big-key") is None
    assert not (tmp_path / "store" / "big-key.md").exists()
    assert not (tmp_path / "e" / "big-key.md").exists()


def test_body_limit_hypothesis(tmp_path: Path) -> None:
    @settings(max_examples=300, deadline=None)
    @given(pad=st.integers(59990, 60004), ch=st.sampled_from(["a", "é", "€", "😀", "\u2028"]),
           tail=st.integers(0, 2))
    def check(pad: int, ch: str, tail: int) -> None:
        body = "y" * pad + ch * tail
        size = len(body.encode())
        backend = mem.FilesBackend(tmp_path / "h")
        if size <= 60000:
            backend.write("hk", H, body)
            assert backend.read("hk").body == body
        else:
            (tmp_path / "h" / "hk.md").unlink(missing_ok=True)
            with pytest.raises(ValueError, match="hk"):
                backend.write("hk", H, body)
            assert not (tmp_path / "h" / "hk.md").exists()

    check()


@pytest.mark.parametrize("header,body", [
    ({"source": "s", "x": "\udcff"}, "b"),
    ({"source": "s", "\udcff": "v"}, "b"),
    ({"source": "s", "n": {"k": ["\x00"]}}, "b"),
    ({"source": "s", "\x00": 1}, "b"),
    (H, "\ud800"),
    (H, "ab\x00"),
])
def test_nul_and_unencodable_refused_before_write(
    tmp_path: Path, header: dict[str, Any], body: str
) -> None:
    fake = FakeBeads()
    for backend in (files_backend(tmp_path), mem.BeadsBackend(fake, tmp_path / "e")):
        with pytest.raises(ValueError, match="nk-1"):
            backend.write("nk-1", header, body)
    assert fake.argv_log == []
    assert not (tmp_path / "store").exists() and not (tmp_path / "e").exists()


# Round 2: canonical line 2 details.


def test_line2_raw_e_accepted_escape_rejected() -> None:
    raw = 'helios-memory 1\n{"source": "é", "status": "active"}\n\nb'
    assert mem.parse("k1", raw).header["source"] == "é"
    esc = 'helios-memory 1\n{"source": "\\u00e9", "status": "active"}\n\nb'
    with pytest.raises(ValueError, match="k1"):
        mem.parse("k1", esc)
    assert mem.serialize({"s": "\x01\u2028"}, "").split("\n")[1] == '{"s": "\\u0001\u2028"}'
    mem.parse("k1", mem.serialize({"s": "\x01\x7f", "status": "active"}, ""))


def test_line2_escape_file_export_raises_beads_skips(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    esc = 'helios-memory 1\n{"source": "\\u00e9", "status": "active"}\n\nb'
    store = tmp_path / "s"
    store.mkdir()
    (store / "esc.md").write_text(esc)
    with pytest.raises(ValueError, match="esc"):
        mem.FilesBackend(store).export(tmp_path / "o")
    with pytest.raises(ValueError, match="esc"):
        mem.FilesBackend(store).read("esc")
    fake = FakeBeads()
    fake.remember("esc", esc)
    mem.BeadsBackend(fake, tmp_path / "e").export(tmp_path / "o2")
    assert "esc" in capsys.readouterr().err
    assert list((tmp_path / "o2").iterdir()) == []


def test_floats_round_trip_canonically(tmp_path: Path) -> None:
    header = {"source": "s", "a": 1.0, "b": 1, "c": 1e5, "d": -0.0, "e": 1e16, "f": 5e-324,
              "g": 0.1, "h": 2**64}
    backend = files_backend(tmp_path)
    backend.write("fl", header, "b")
    got = backend.read("fl").header
    for k in "abcdefgh":
        assert got[k] == header[k] and type(got[k]) is type(header[k]), k
    assert str(got["d"]) == "-0.0"
    raw = (tmp_path / "store" / "fl.md").read_bytes()
    assert mem.serialize(got, "b").encode() == raw
    for hand in ('{"c": 1e5, "source": "s", "status": "active"}',
                 '{"a": 1.00, "source": "s", "status": "active"}',
                 '{"a": 1E+16, "source": "s", "status": "active"}',
                 '{"d": -0, "source": "s", "status": "active"}'):
        text = f"helios-memory 1\n{hand}\n\nb"
        try:
            parsed = mem.parse("fk", text)
        except ValueError as exc:
            assert "fk" in str(exc)
        else:
            assert mem.serialize(parsed.header, parsed.body) == text, hand


@pytest.mark.parametrize("line2", [
    '{"n": ' + "9" * 5000 + ', "source": "s", "status": "active"}',
    '{"n": ' + "[" * 100000 + "]" * 100000 + ', "source": "s", "status": "active"}',
])
def test_pathological_line2_raises_valueerror_naming_key(line2: str) -> None:
    text = f"helios-memory 1\n{line2}\n\nb"
    try:
        mem.parse("path-key", text)
    except ValueError as exc:
        assert "path-key" in str(exc), f"ValueError without key: {str(exc)[:120]}"
    except BaseException as exc:  # noqa: BLE001
        pytest.fail(f"{type(exc).__name__} instead of ValueError naming the key")
    else:
        pytest.fail("accepted")


def test_pathological_file_in_files_stale_gets_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "s"
    store.mkdir()
    deep = "[" * 100000 + "]" * 100000
    (store / "deep.md").write_text(
        f'helios-memory 1\n{{"n": {deep}, "source": "s", "status": "active"}}\n\nb')
    try:
        assert mem.FilesBackend(store, labels_of=lambda b: None).stale() == []
    except RecursionError:
        pytest.fail("stale() crashed with RecursionError instead of printing the skip note")
    assert "deep" in capsys.readouterr().err


# Round 3: a header nested deep enough to blow Python's own recursion limit
# (json's decoder and encoder tolerate far deeper) must never crash with a
# bare RecursionError; it either round-trips or raises ValueError naming the
# key before anything is written (decided).


def _nested_list(depth: int) -> list[Any]:
    root: list[Any] = []
    cur = root
    for _ in range(depth - 1):
        nxt: list[Any] = []
        cur.append(nxt)
        cur = nxt
    return root


@pytest.mark.parametrize("depth", [1000, 3000, 9000])
def test_deep_header_write_never_crashes(tmp_path: Path, depth: int) -> None:
    nested = _nested_list(depth)
    fake = FakeBeads()
    for backend in (mem.FilesBackend(tmp_path / "s"), mem.BeadsBackend(fake, tmp_path / "e")):
        try:
            backend.write("dw", {"source": "s", "n": nested}, "b")
        except ValueError as exc:
            assert "'dw'" in str(exc)
        else:
            assert backend.read("dw").header["n"] == nested


@pytest.mark.parametrize("depth", [1000, 3000, 9000])
def test_deep_header_import_never_crashes(tmp_path: Path, depth: int) -> None:
    nested = _nested_list(depth)
    text = mem.serialize({"source": "s", "n": nested, "status": "active"}, "b")
    src = tmp_path / "src"
    src.mkdir()
    (src / "d.md").write_bytes(text.encode())
    for backend in (mem.FilesBackend(tmp_path / "s"), mem.BeadsBackend(FakeBeads(), tmp_path / "e")):
        try:
            backend.import_(src)
        except ValueError as exc:
            assert "'d'" in str(exc)
        else:
            assert backend.read("d").header["n"] == nested


# Round 2: import_ checks every file before writing any.


FULL = {"source": "s#1", "status": "active"}

_BAD_LAST = {
    "noncanonical": ("z.md", b'helios-memory 1\n{"source":"s","status":"active"}\n\nz'),
    "no-source": ("z.md", canon({"status": "active"}, "z")),
    "empty-source": ("z.md", canon({"source": "", "status": "active"}, "z")),
    "bad-status": ("z.md", canon({"source": "s", "status": "bogus"}, "z")),
    "body-60001": ("z.md", canon(FULL, "y" * 60001)),
    "body-straddle": ("z.md", canon(FULL, "y" * 59999 + "é")),
    "nul-body": ("z.md", canon(FULL, "z\x00")),
    "nul-header": ("z.md", canon({"source": "s\x00", "status": "active"}, "z")),
    "not-utf8": ("z.md", b"helios-memory 1\n{}\n\n\xff"),
    "bad-name": ("z_Z.md", canon(FULL, "z")),
}


def _src_with_bad_last(tmp_path: Path, case: str) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_bytes(canon(FULL, "a"))
    (src / "b.md").write_bytes(canon(FULL, "b"))
    name, data = _BAD_LAST[case]
    (src / name).write_bytes(data)
    return src


@pytest.mark.parametrize("case", sorted(_BAD_LAST))
def test_import_bad_last_files_writes_nothing(tmp_path: Path, case: str) -> None:
    src = _src_with_bad_last(tmp_path, case)
    store = tmp_path / "store"
    with pytest.raises(ValueError) as excinfo:
        mem.FilesBackend(store).import_(src)
    assert "z" in str(excinfo.value)
    written = sorted(p.name for p in store.glob("*")) if store.exists() else []
    assert written == [], f"{case}: import_ wrote {written} before failing"


@pytest.mark.parametrize("case", sorted(_BAD_LAST))
def test_import_bad_last_fake_bd_writes_nothing(tmp_path: Path, case: str) -> None:
    src = _src_with_bad_last(tmp_path, case)
    fake = FakeBeads()
    with pytest.raises(ValueError):
        mem.BeadsBackend(fake, tmp_path / "e").import_(src)
    assert fake.argv_log == [], f"{case}: bd remember called for {[a[2] for a in fake.argv_log]}"
    assert not (tmp_path / "e").exists() or list((tmp_path / "e").iterdir()) == []


def test_missing_status_file_is_bad(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "k.md").write_bytes(canon({"source": "s"}, "b"))
    with pytest.raises(ValueError) as excinfo:
        files_backend(tmp_path).import_(src)
    assert "k" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        mem.parse("k", canon({"source": "s"}, "b").decode())
    assert "k" in str(excinfo.value)


# Round 3: parse requires status to be one of the listed values, so export
# after import stays byte for byte (decided).


@pytest.mark.parametrize("status", ["bogus", 1, None])
def test_parse_read_export_reject_bad_status_value(tmp_path: Path, status: Any) -> None:
    text = mem.serialize({"source": "s", "status": status}, "b")
    with pytest.raises(ValueError) as excinfo:
        mem.parse("bs", text)
    assert "bs" in str(excinfo.value)
    store = tmp_path / "s"
    store.mkdir()
    (store / "bs.md").write_bytes(text.encode())
    with pytest.raises(ValueError) as excinfo:
        mem.FilesBackend(store).read("bs")
    assert "bs" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        mem.FilesBackend(store).export(tmp_path / "o")
    assert "bs" in str(excinfo.value)


# Round 2: the whole value is at most 65000 bytes.


def test_value_size_limit_boundary(tmp_path: Path) -> None:
    base = mem.serialize({"source": "s", "status": "active", "pad": ""}, "y" * 100)
    room = 65000 - len(base.encode())
    assert room > 0
    backend = files_backend(tmp_path)
    backend.write("edge", {"source": "s", "status": "active", "pad": "h" * room}, "y" * 100)
    assert len((tmp_path / "store" / "edge.md").read_bytes()) == 65000
    fake = FakeBeads()
    beads_backend = mem.BeadsBackend(fake, tmp_path / "e")
    with pytest.raises(ValueError) as excinfo:
        backend.write("over", {"source": "s", "status": "active", "pad": "h" * (room + 1)}, "y" * 100)
    assert "over" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        beads_backend.write("over", {"source": "s", "status": "active", "pad": "h" * (room + 1)},
                            "y" * 100)
    assert "over" in str(excinfo.value)
    assert fake.recall("over") is None
    assert not (tmp_path / "store" / "over.md").exists()
    assert not (tmp_path / "e").exists()


# Round 2: stale() narrows KeyError to the missing-bead signal.


def test_labels_from_show_fake_beads_missing_is_missing() -> None:
    """FakeBeads.show raises BeadNotFound for a missing bead; that reads as missing."""
    lookup = mem._labels_from_show(FakeBeads().show)
    assert lookup("hel-404") is None


def test_labels_from_show_plain_key_error_propagates() -> None:
    """A show raising a plain KeyError(bead_id), not BeadNotFound, propagates."""
    def show(bead_id: str) -> Bead:
        raise KeyError(bead_id)

    lookup = mem._labels_from_show(show)
    with pytest.raises(KeyError):
        lookup("hel-1")


def test_labels_from_show_wrong_id_is_missing() -> None:
    """A show returning a Bead whose id differs from the request reads as missing."""
    def show(bead_id: str) -> Bead:
        return Bead(id="hel-other", labels=["truth:wrong"])

    lookup = mem._labels_from_show(show)
    assert lookup("hel-1") is None


def test_stale_malformed_show_shapes_propagate(tmp_path: Path) -> None:
    """A show that returns id-less objects or raises a foreign KeyError is not a miss."""
    def no_id(bead_id: str) -> Bead:
        raise KeyError("id")

    def wrong_shape(bead_id: str) -> Bead:
        raise KeyError(0)

    def boom(bead_id: str) -> Bead:
        raise RuntimeError("bd not installed")

    for show in (no_id, wrong_shape, boom):
        backend = mem.FilesBackend(tmp_path / "s", labels_of=mem._labels_from_show(show))
        backend.write("x", {"source": "hel-1#1"}, "b")
        with pytest.raises(Exception):
            backend.stale()


# Round 3: a flag-like bead id in source is a bead that does not exist; the
# label lookup is never called for it (decided).


@pytest.mark.parametrize("source", ["-h#1", "--json#1", "#1", " #1"])
def test_stale_flag_like_bead_id_never_calls_lookup(tmp_path: Path, source: str) -> None:
    calls: list[str] = []

    def labels_of(bead_id: str) -> list[str] | None:
        calls.append(bead_id)
        return ["truth:wrong"]

    backend = mem.FilesBackend(tmp_path / "s", labels_of=labels_of)
    backend.write("k", {"source": source}, "b")
    assert backend.stale() == []
    assert calls == []


# Round 2: real bd coverage.

REAL_BD = shutil.which("bd")
needs_bd = pytest.mark.skipif(REAL_BD is None, reason="bd not on PATH")


def bd_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    subprocess.run(["bd", "init", "--non-interactive"], cwd=root, check=True, capture_output=True)
    return root


@pytest.fixture(scope="module")
def shared_bd(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return bd_repo(tmp_path_factory.mktemp("bdshared"))


def _create_bead(repo: Path, labels: list[str]) -> str:
    argv = ["bd", "create", "--title", "t", "--type", "task", "--silent"]
    if labels:
        argv += ["--labels", ",".join(labels)]
    return subprocess.run(argv, cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="module")
def stale_repo(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str, str]:
    repo = bd_repo(tmp_path_factory.mktemp("bdstale"))
    wrong = _create_bead(repo, ["truth:wrong"])
    fine = _create_bead(repo, [])
    return repo, wrong, fine


@needs_bd
@pytest.mark.parametrize("key", ["x" * 200, "a.", "a..b", "a_b-c.d"])
def test_key_real_bd_round_trip(shared_bd: Path, tmp_path: Path, key: str) -> None:
    backend = mem.BeadsBackend(Beads(shared_bd), tmp_path / "e")
    backend.write(key, H, "body\n")
    assert backend.read(key).body == "body\n"
    backend.export(tmp_path / "o")
    assert (tmp_path / "o" / f"{key}.md").read_bytes() == canon(
        {**H, "status": "active"}, "body\n")


@needs_bd
@pytest.mark.parametrize("case", ["no-source", "body-60001", "noncanonical"])
def test_import_bad_last_real_bd_writes_nothing(tmp_path: Path, case: str) -> None:
    repo = bd_repo(tmp_path / "repo")
    src = _src_with_bad_last(tmp_path, case)
    beads = Beads(repo)
    with pytest.raises(ValueError):
        mem.BeadsBackend(beads, tmp_path / "e").import_(src)
    assert beads.memories() == {}, f"{case}: bd holds {sorted(beads.memories())}"


@needs_bd
@pytest.mark.parametrize("ch", ["é", "€", "😀"])
def test_body_limit_real_bd(shared_bd: Path, tmp_path: Path, ch: str) -> None:
    beads = Beads(shared_bd)
    backend = mem.BeadsBackend(beads, tmp_path / "e")
    key = f"lim-{len(ch.encode())}"
    body = _ok_body(ch)
    backend.write(key, H, body)
    assert backend.read(key).body == body
    assert beads.memories()[key] == mem.serialize({**H, "status": "active"}, body)
    for i, bad in enumerate(_over_bodies(ch)):
        bkey = f"over-{len(ch.encode())}-{i}"
        with pytest.raises(ValueError, match=bkey):
            backend.write(bkey, H, bad)
        assert beads.recall(bkey) is None
        assert not (tmp_path / "e" / f"{bkey}.md").exists()


@needs_bd
def test_stale_real_bd_exact_and_partial(
    stale_repo: tuple[Path, str, str], tmp_path: Path
) -> None:
    repo, wrong, fine = stale_repo
    fake_store = FakeBeads()
    backend = mem.BeadsBackend(
        fake_store, tmp_path / "e2", labels_of=mem._labels_from_show(Beads(repo).show))
    suffix = wrong.rsplit("-", 1)[1]
    backend.write("exact", {"source": f"{wrong}#1"}, "x")
    backend.write("partial", {"source": f"{suffix}#1"}, "x")
    backend.write("fine", {"source": f"{fine}#1"}, "x")
    backend.write("gone", {"source": f"{wrong}#1", "status": "retracted"}, "x")
    with pytest.raises(BeadNotFound):
        Beads(repo).show(suffix)  # bd's partial resolution now reads as missing
    assert backend.stale() == ["exact"]


@needs_bd
@pytest.mark.parametrize("show_script", [
    'echo "Error: database is locked" >&2; exit 1',
    'echo \'{"error": "connection refused", "schema_version": 1}\'; exit 1',
    'echo "not json"; exit 0',
    'echo \'[{"labels": ["truth:wrong"]}]\'; exit 0',
    'echo \'{"error": "boom"}\'; exit 0',
])
def test_stale_shim_other_failure_propagates(
    stale_repo: tuple[Path, str, str], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, show_script: str
) -> None:
    repo, wrong, _ = stale_repo
    bindir = tmp_path / "shimbin"
    bindir.mkdir()
    shim = bindir / "bd"
    shim.write_text(
        f'#!/bin/sh\nif [ "$1" = "show" ]; then\n{show_script}\nfi\nexec "{REAL_BD}" "$@"\n')
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    backend = mem.open_backend(
        Config(hub=repo, memory=MemoryConfig(backend="files", export_dir="shim")))
    backend.write("sh", {"source": f"{wrong}#1"}, "x")
    with pytest.raises(Exception):
        backend.stale()


@needs_bd
def test_crash_between_bd_and_export_file_real_bd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = bd_repo(tmp_path / "repo")
    exp = tmp_path / "e"
    backend = mem.BeadsBackend(Beads(repo), exp)
    backend.write("k", H, "v1")
    real = mem._write_bytes_atomic

    def boom(*a: Any, **kw: Any) -> None:
        raise OSError("simulated crash after bd remember")

    monkeypatch.setattr(mem, "_write_bytes_atomic", boom)
    with pytest.raises(OSError):
        backend.write("k", H, "v2\n")
    with pytest.raises(OSError):
        backend.write("new", H, "first")
    monkeypatch.setattr(mem, "_write_bytes_atomic", real)
    assert backend.read("k").body == "v2\n"
    assert backend.read("new").body == "first"
    assert (exp / "k.md").read_bytes() == canon({**H, "status": "active"}, "v1")
    assert not (exp / "new.md").exists()
    backend.export(exp)
    assert (exp / "k.md").read_bytes() == canon({**H, "status": "active"}, "v2\n")
    assert (exp / "new.md").read_bytes() == canon({**H, "status": "active"}, "first")
    assert sorted(p.name for p in exp.iterdir()) == ["k.md", "new.md"]
    assert mem.BeadsBackend(Beads(repo), tmp_path / "e3").inject(["new", "k"]) == \
        "### new\n\nfirst\n\n### k\n\nv2\n"


@needs_bd
@pytest.mark.parametrize("n", range(0, 8))
def test_truncated_echo_real_bd(shared_bd: Path, tmp_path: Path, n: int) -> None:
    backend = mem.BeadsBackend(Beads(shared_bd), tmp_path / "e")
    for ch in ("é", "€", "😀"):
        body = "a" * n + ch * 200
        key = f"echo-{n}-{len(ch.encode())}"
        backend.write(key, {"source": "s" * (n + 1)}, body)
        assert backend.read(key).body == body


_TEXT = st.text(alphabet=st.characters(codec="utf-8", exclude_characters="\x00"), max_size=60)


@needs_bd
def test_hypothesis_real_bd_round_trip(shared_bd: Path, tmp_path: Path) -> None:
    beads = Beads(shared_bd)
    counter = [0]

    @settings(max_examples=40, deadline=None)
    @given(header=st.dictionaries(_TEXT.filter(lambda k: k not in ("source", "status")), _TEXT,
                                  max_size=3),
           body=st.one_of(_TEXT, st.sampled_from(["", "\n", "\n\n", "\r\n", " \t\n", "-x", "--",
                                                   "\ufeff\u2028\x1b[0m\x7f"])))
    def check(header: dict[str, str], body: str) -> None:
        counter[0] += 1
        key = f"hyp-{counter[0]}"
        backend = mem.BeadsBackend(beads, tmp_path / "e")
        full = {**header, "source": "s#1", "status": "active"}
        backend.write(key, full, body)
        got = backend.read(key)
        assert (got.header, got.body) == (full, body)
        assert beads.memories()[key] == mem.serialize(full, body)
        assert (tmp_path / "e" / f"{key}.md").read_bytes() == canon(full, body)

    check()


@needs_bd
@pytest.mark.parametrize("pad", [5000, 70000, 300000])
def test_large_header_real_bd(shared_bd: Path, tmp_path: Path, pad: int) -> None:
    """No SPEC limit on header size. A value bd cannot keep must fail loudly, never truncate."""
    beads = Beads(shared_bd)
    backend = mem.BeadsBackend(beads, tmp_path / "e")
    key = f"bighead-{pad}"
    header = {"source": "s", "pad": "h" * pad}
    body = "y" * 60000
    try:
        backend.write(key, header, body)
    except Exception as exc:  # noqa: BLE001
        print(f"write refused loudly: {type(exc).__name__}: {str(exc)[:200]}")
        full = mem.serialize({**header, "status": "active"}, body)
        assert beads.recall(key) is None or beads.recall(key) == full
        return
    got = backend.read(key)
    assert got.body == body and got.header["pad"] == header["pad"]


@needs_bd
def test_beads_export_skips_foreign_keys_with_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = bd_repo(tmp_path / "repo")
    for key, value in (("-a", mem.serialize(FULL, "x")), ("Key", mem.serialize(FULL, "x")),
                       ("plain", "hello")):
        subprocess.run(["bd", "remember", "--key", key, "--", value], cwd=repo, check=True,
                       capture_output=True)
    backend = mem.BeadsBackend(Beads(repo), tmp_path / "e", labels_of=lambda b: None)
    backend.write("good", H, "g")
    backend.export(tmp_path / "o")
    err = capsys.readouterr().err
    assert [p.name for p in (tmp_path / "o").iterdir()] == ["good.md"]
    for key in ("-a", "Key", "plain"):
        assert repr(key) in err
    backend.stale()
    err = capsys.readouterr().err
    for key in ("-a", "Key", "plain"):
        assert repr(key) in err
