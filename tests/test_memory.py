"""Memory backend tests (SPEC §13)."""

from __future__ import annotations

import json
import math
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
from helios.beads import Bead, Beads, FakeBeads
from helios.config import Config, MemoryConfig, load

KEY = "probe"
H = {"source": "hel-a02#1"}


def files_backend(tmp_path: Path) -> mem.FilesBackend:
    return mem.FilesBackend(tmp_path / "store")


def sample_header(**extra: Any) -> dict[str, Any]:
    header: dict[str, Any] = {"source": "hel-a02#1"}
    header.update(extra)
    return header


def canon(key: str, header: dict[str, Any], body: str) -> bytes:
    return mem.serialize(header, body).encode("utf-8")


# 1) Format: serialize and parse are exact inverses.


@settings(max_examples=200)
@given(
    header=st.dictionaries(st.text(), st.text(), max_size=4),
    body=st.text(alphabet=st.characters(codec=None, blacklist_categories=())),
)
def test_format_inverse_any_text(header: dict[str, Any], body: str) -> None:
    text = mem.serialize(header, body)
    parsed = mem.parse("k", text)
    assert (parsed.header, parsed.body) == (header, body)
    assert mem.serialize(parsed.header, parsed.body) == text


@pytest.mark.parametrize(
    "body",
    ["", "x", "x\n", "x\n\n", "a\r\n", "a\r", "\u2028", "\x00",
     "helios-memory 1\n{}\n\n", "y" * 1_000_000],
)
def test_format_inverse_edges(body: str) -> None:
    parsed = mem.parse("k", mem.serialize({"source": "s"}, body))
    assert parsed.body == body


def test_nan_inf_header_bytes_stable() -> None:
    text = mem.serialize({"source": "s", "n": float("nan"), "i": 1e400}, "b")
    parsed = mem.parse("k", text)
    assert math.isnan(parsed.header["n"]) and parsed.header["i"] == float("inf")
    assert mem.serialize(parsed.header, parsed.body) == text


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


@pytest.mark.parametrize("key", ["a", "a0", "k.e-y_z", "0abc", "a.", "a..", "a.md"])
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
     "é", "ａ", "٣", "-a", ".a", "_a", "a*", "x" * 201],
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
    assert path.read_bytes() == canon("m1", header, "line1\nline2\n")
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
    (dir_a / "extra.md").write_bytes(canon("extra", {"source": "x#1", "status": "active"}, "kept"))
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
    raw = canon("k", {"source": "s", "status": "active"}, "b")
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
        (src / f"{name}.md").write_bytes(canon(name, {"source": "s", "status": "active"}, name))
    (src / "dir.md").mkdir()
    (src / "dir.md" / "inner.md").write_bytes(canon("inner", {"source": "s"}, "i"))
    (src / "broken.md").symlink_to(tmp_path / "nowhere.md")
    (src / "x.MD").write_bytes(canon("x", {"source": "s"}, "x"))
    (src / "y.md.txt").write_bytes(b"junk")
    (src / ".hidden.md").write_bytes(b"junk")
    order: list[str] = []

    class Rec(mem.FilesBackend):
        def write(self, key: str, header: dict[str, Any], body: str) -> None:
            order.append(key)
            super().write(key, header, body)

    Rec(tmp_path / "store").import_(src)
    assert order == ["a", "a-b", "a.b", "b"]


def test_import_bad_file_writes_nothing(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_bytes(canon("a", {"source": "s", "status": "active"}, "a"))
    (src / "z.md").write_bytes(canon("z", {"source": "s", "status": "active"}, "z"))
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
    (src / "ok.md").write_bytes(canon("ok", {"source": "s#1", "status": "active"}, "ok"))
    (src / "notes.txt").write_text("not a memory")
    sub = src / "sub"
    sub.mkdir()
    (sub / "inner.md").write_bytes(canon("inner", {"source": "s#1", "status": "active"}, "i"))
    backend = files_backend(tmp_path)
    backend.import_(src)
    assert backend.read("ok").body == "ok"
    with pytest.raises(KeyError):
        backend.read("inner")
    with pytest.raises(KeyError):
        backend.read("notes")


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
    assert stored == canon(KEY, {"source": "hel-a02#1", "status": "active"}, "b").decode()
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
        "k", {**H, "status": "active"}, "v2"
    )
    backend.export(tmp_path / "e")  # healing export into export_dir
    assert (tmp_path / "e" / "k.md").read_bytes() == canon("k", {**H, "status": "active"}, "v2")
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


def test_beads_schema_version_key_fake(tmp_path: Path) -> None:
    fake = FakeBeads()
    backend = mem.BeadsBackend(fake, tmp_path / "e")
    backend.write("schema_version", H, "b")
    backend.export(tmp_path / "o")
    assert (tmp_path / "o" / "schema_version.md").exists()


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
