"""Memory backend tests (SPEC §13)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from helios import memory as mem
from helios.beads import Beads, FakeBeads
from helios.config import Config, MemoryConfig

KEY = "probe"


def write_files_backend(tmp_path: Path) -> mem.FilesBackend:
    return mem.FilesBackend(tmp_path / "store")


def sample_header(**extra: Any) -> dict[str, Any]:
    header: dict[str, Any] = {"source": "hel-a02#1"}
    header.update(extra)
    return header


# 1) Format: serialize and parse are exact inverses.

json_value = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.text(),
        st.floats(allow_nan=False, allow_infinity=False),
    ),
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(st.text(max_size=8), children, max_size=3),
    ),
    max_leaves=4,
)


@given(
    header=st.dictionaries(st.text(min_size=1, max_size=12), json_value, max_size=5),
    body=st.text(),
)
def test_format_round_trip(header: dict[str, Any], body: str) -> None:
    text = mem.serialize(header, body)
    parsed = mem.parse(KEY, text)
    assert parsed.key == KEY
    assert parsed.header == header
    assert parsed.body == body
    assert mem.serialize(parsed.header, parsed.body) == text


def test_format_round_trip_edge_bodies() -> None:
    for body in ["", "no trailing newline", "a\n", "a\n\n", "a\r\nb\r\n", "héllo wörld\n"]:
        header = {"source": "s#1", "status": "active", "unit": "U14"}
        parsed = mem.parse(KEY, mem.serialize(header, body))
        assert parsed.body == body
        assert parsed.header == header


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
        "x\nhelios-memory 1\n{}\n\nbody",
        "helios-memory 1\n{}\n",
        "helios-memory 1\n{}\nbody",
        "helios-memory 1\n{bad}\n\nbody",
        "helios-memory 1\n[1, 2]\n\nbody",
        'helios-memory 1\n"str"\n\nbody',
        "helios-memory 1\n42\n\nbody",
        "helios-memory 1\nnull\n\nbody",
        "helios-memory 1\n\n\nbody",
    ],
)
def test_format_malformed_prefix_raises_naming_key(text: str) -> None:
    with pytest.raises(ValueError, match="my-key"):
        mem.parse("my-key", text)


# 2) Key rule and every write refusal.

VALID_KEYS = ["a", "A0", "k.e-y_z", "0abc", "x" * 64]


@pytest.mark.parametrize("key", VALID_KEYS)
def test_key_rule_accepts(tmp_path: Path, key: str) -> None:
    backend = write_files_backend(tmp_path)
    backend.write(key, sample_header(), "body")
    assert backend.read(key).body == "body"


@pytest.mark.parametrize(
    "key", ["", "-a", ".a", "_a", "a b", "a/b", "a:b", "../x", "é", "a*"]
)
def test_key_rule_rejects(tmp_path: Path, key: str) -> None:
    backend = write_files_backend(tmp_path)
    with pytest.raises(ValueError) as excinfo:
        backend.write(key, sample_header(), "body")
    assert key in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        backend.read(key)
    assert key in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        backend.inject([key])
    assert key in str(excinfo.value)


@pytest.mark.parametrize("header", [{}, {"source": ""}, {"source": 3}, {"source": None}])
def test_write_refuses_bad_source(tmp_path: Path, header: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="source"):
        write_files_backend(tmp_path).write(KEY, header, "body")


def test_write_refuses_bad_status(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bogus"):
        write_files_backend(tmp_path).write(KEY, sample_header(status="bogus"), "body")


def test_write_defaults_missing_status_to_active(tmp_path: Path) -> None:
    backend = write_files_backend(tmp_path)
    backend.write(KEY, {"source": "s#1", "unit": "U14"}, "body")
    stored = backend.read(KEY)
    assert stored.header["status"] == "active"
    assert stored.header["unit"] == "U14"


def test_write_refuses_under_helios_bead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HELIOS_BEAD", "hel-x")
    with pytest.raises(PermissionError):
        write_files_backend(tmp_path).write(KEY, sample_header(), "body")
    with pytest.raises(PermissionError):
        mem.BeadsBackend(FakeBeads(), tmp_path / "exp").write(
            KEY, sample_header(), "body"
        )


# 3) files backend read, write, export, import_.


def test_files_backend_exact_bytes_and_missing_key(tmp_path: Path) -> None:
    backend = write_files_backend(tmp_path)
    header = sample_header(status="active", unit="U14")
    backend.write("m1", header, "line1\nline2\n")
    path = tmp_path / "store" / "m1.md"
    assert path.read_bytes() == mem.serialize({**header, "status": "active"}, "line1\nline2\n").encode()
    assert backend.read("m1") == mem.Memory(key="m1", header={**header}, body="line1\nline2\n")
    with pytest.raises(KeyError):
        backend.read("nope")


def test_files_export_import_round_trip_byte_for_byte(tmp_path: Path) -> None:
    backend = write_files_backend(tmp_path)
    backend.write("a", sample_header(status="active"), "no newline")
    backend.write("b", sample_header(status="retracted"), "one\n")
    backend.write("c", {"source": "hel-9#3", "note": "héllo\r\n"}, "")
    dir_a = tmp_path / "a"
    backend.export(dir_a)
    assert sorted(p.name for p in dir_a.iterdir()) == ["a.md", "b.md", "c.md"]
    (dir_a / "extra.md").write_bytes(
        mem.serialize({"source": "x#1", "status": "active"}, "kept").encode()
    )
    backend.export(dir_a)  # never deletes files
    assert (dir_a / "extra.md").is_file()

    fresh = mem.FilesBackend(tmp_path / "fresh")
    fresh.import_(dir_a)
    assert fresh.read("extra").body == "kept"
    dir_b = tmp_path / "b"
    fresh.export(dir_b)
    assert sorted(p.name for p in dir_b.iterdir()) == sorted(
        p.name for p in dir_a.iterdir()
    )
    for path in dir_a.iterdir():
        assert (dir_b / path.name).read_bytes() == path.read_bytes()


def test_files_import_malformed_raises_naming_key(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.md").write_text("not a memory value")
    with pytest.raises(ValueError) as excinfo:
        write_files_backend(tmp_path).import_(src)
    assert "bad" in str(excinfo.value)


def test_files_import_ignores_subdirs_and_non_md(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.md").write_bytes(mem.serialize({"source": "s#1"}, "ok").encode())
    (src / "notes.txt").write_text("not a memory")
    sub = src / "sub"
    sub.mkdir()
    (sub / "inner.md").write_bytes(mem.serialize({"source": "s#1"}, "inner").encode())
    backend = write_files_backend(tmp_path)
    backend.import_(src)
    assert backend.read("ok").body == "ok"
    with pytest.raises(KeyError):
        backend.read("inner")
    with pytest.raises(KeyError):
        backend.read("notes")


# 4) beads backend with FakeBeads.


def test_beads_write_calls_bd_before_export_file(tmp_path: Path) -> None:
    export_dir = tmp_path / "exp"

    class FailingBeads(FakeBeads):
        def remember(self, key: str, value: str) -> None:
            raise RuntimeError("bd is down")

    with pytest.raises(RuntimeError, match="bd is down"):
        mem.BeadsBackend(FailingBeads(), export_dir).write(KEY, sample_header(), "b")
    assert not (export_dir / f"{KEY}.md").exists()

    fake = FakeBeads()
    backend = mem.BeadsBackend(fake, export_dir)
    backend.write(KEY, sample_header(), "b")
    stored = fake.recall(KEY)
    assert stored == mem.serialize({"source": "hel-a02#1", "status": "active"}, "b")
    assert stored is not None
    assert (export_dir / f"{KEY}.md").read_bytes() == stored.encode()


def test_beads_export_skips_non_memory_values_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeBeads()
    backend = mem.BeadsBackend(fake, tmp_path / "exp")
    backend.write("good", sample_header(), "body")
    fake.remember("junk", "not a memory value")
    out = tmp_path / "out"
    backend.export(out)
    assert (out / "good.md").is_file()
    assert not (out / "junk.md").exists()
    err = capsys.readouterr().err
    assert "junk" in err


BD = shutil.which("bd")


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_beads_real_bd_round_trip_trailing_newlines(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["bd", "init", "--non-interactive"], cwd=tmp_path, check=True, capture_output=True
    )
    backend = mem.BeadsBackend(Beads(tmp_path), tmp_path / "exp")
    bodies = {"no-newline": "text", "one": "text\n", "two": "text\n\n"}
    for i, (name, body) in enumerate(bodies.items()):
        backend.write(f"k{i}", sample_header(), body)
    for i, (name, body) in enumerate(bodies.items()):
        assert backend.read(f"k{i}").body == body


# 5) stale with an injected label lookup.


def stale_setup(tmp_path: Path) -> mem.FilesBackend:
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
    assert stale_setup(tmp_path).stale() == ["m0", "m1"]


def test_beads_stale_uses_injected_lookup(tmp_path: Path) -> None:
    backend = mem.BeadsBackend(
        FakeBeads(),
        tmp_path / "exp",
        labels_of=lambda bead_id: ["truth:wrong"] if bead_id == "b9" else None,
    )
    backend.write("s1", {"source": "b9#1"}, "x")
    backend.write("s2", {"source": "other#1"}, "x")
    assert backend.stale() == ["s1"]


# 6) inject exact bytes and KeyError.


def test_inject_exact_bytes_and_missing_key(tmp_path: Path) -> None:
    backend = write_files_backend(tmp_path)
    backend.write("a", sample_header(), "body-a")
    backend.write("b", sample_header(), "body-b\n")
    assert backend.inject(["b", "a"]) == "### b\n\nbody-b\n\n\n### a\n\nbody-a"
    with pytest.raises(KeyError):
        backend.inject(["a", "gone"])


# 7) open_backend.


def test_open_backend(tmp_path: Path) -> None:
    beads_backend = mem.open_backend(
        Config(hub=tmp_path, memory=MemoryConfig(backend="beads"))
    )
    assert isinstance(beads_backend, mem.BeadsBackend)
    files_backend = mem.open_backend(
        Config(hub=tmp_path, memory=MemoryConfig(backend="files"))
    )
    assert isinstance(files_backend, mem.FilesBackend)
    with pytest.raises(ValueError, match="unknown memory backend 'zzz'"):
        mem.open_backend(Config(hub=tmp_path, memory=MemoryConfig(backend="zzz")))
