"""Memory backends (SPEC §13).

Value format version 1, lossless: line 1 is ``helios-memory 1``, line 2 is one
JSON object in canonical form, line 3 is empty, the body follows verbatim.
``FilesBackend`` keeps ``<dir>/<key>.md`` files; ``BeadsBackend`` keeps values
in ``bd`` (through ``helios.beads``) with a files export under
``memory.export_dir``. Only the orchestrator writes: ``write`` and ``import_``
refuse while ``HELIOS_BEAD`` is set.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, NoReturn, Protocol, Sequence

from helios.beads import Beads, BeadNotFound
from helios.config import Config

MAGIC = "helios-memory 1"

_KEY_PATTERN = r"[a-z0-9][a-z0-9._-]{0,199}"

_BEAD_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}"

_RESERVED_KEYS = frozenset({"schema_version"})

_STATUSES = ("active", "superseded", "retracted")

_BODY_LIMIT_BYTES = 60000
_VALUE_LIMIT_BYTES = 65000

LabelsOf = Callable[[str], Sequence[str] | None]


@dataclass(frozen=True)
class Memory:
    """One memory: its key, the line 2 object as a dict, and the verbatim body."""

    key: str
    header: dict[str, Any]
    body: str


class Backend(Protocol):
    """The memory interface of SPEC §13."""

    def inject(self, keys: Sequence[str]) -> str: ...
    def write(self, key: str, header: dict[str, Any], body: str) -> None: ...
    def read(self, key: str) -> Memory: ...
    def stale(self) -> list[str]: ...
    def export(self, directory: Path | str) -> None: ...
    def import_(self, directory: Path | str) -> None: ...


class _BdMemories(Protocol):
    def remember(self, key: str, value: str) -> None: ...
    def recall(self, key: str) -> str | None: ...
    def memories(self) -> dict[str, str]: ...


def serialize(header: dict[str, Any], body: str) -> str:
    """Render the version 1 value: magic line, canonical JSON, blank, body (SPEC §13).

    NaN and Infinity are refused anywhere in the header: they are not JSON,
    so no canonical line 2 could hold them.
    """
    line2 = json.dumps(header, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return f"{MAGIC}\n{line2}\n\n{body}"


def _reject_constant(value: str) -> NoReturn:
    """``parse_constant`` for loads: NaN and Infinity are not JSON (SPEC §13)."""
    raise ValueError(f"non-JSON constant {value}")


def parse(key: str, text: str) -> Memory:
    """Split a version 1 value back into header and verbatim body (SPEC §13).

    Line 2 must equal exactly the canonical dump of the object it parses to,
    and must carry a ``status`` key; anything else raises ValueError naming
    the key.
    """
    prefix = MAGIC + "\n"
    if not text.startswith(prefix):
        raise ValueError(f"memory {key!r}: must start with {MAGIC!r} line")
    rest = text[len(prefix) :]
    nl = rest.find("\n")
    if nl == -1:
        raise ValueError(f"memory {key!r}: missing header JSON line")
    line2 = rest[:nl]
    tail = rest[nl + 1 :]
    if not tail.startswith("\n"):
        raise ValueError(f"memory {key!r}: missing blank line after header")
    try:
        header = json.loads(line2, parse_constant=_reject_constant)
    except RecursionError as exc:
        raise ValueError(f"memory {key!r}: header JSON is too deeply nested") from exc
    except ValueError as exc:
        raise ValueError(f"memory {key!r}: bad header JSON: {exc}") from exc
    if not isinstance(header, dict):
        raise ValueError(f"memory {key!r}: header must be a JSON object")
    if "status" not in header:
        raise ValueError(f"memory {key!r}: header needs 'status'")
    if header["status"] not in _STATUSES:
        raise ValueError(f"memory {key!r}: bad status {header['status']!r}")
    try:
        canonical = json.dumps(header, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (RecursionError, ValueError) as exc:
        raise ValueError(f"memory {key!r}: header is not canonical: {exc}") from exc
    if line2 != canonical:
        raise ValueError(f"memory {key!r}: header line is not canonical")
    return Memory(key=key, header=header, body=tail[1:])


def _check_key(key: str) -> None:
    """Raise ValueError naming the key unless it matches the SPEC §13 key rule.

    ``schema_version`` is refused: bd hides a memory with that key from
    ``bd memories``, so it could never round-trip.
    """
    if key in _RESERVED_KEYS or re.fullmatch(_KEY_PATTERN, key) is None:
        raise ValueError(f"invalid memory key {key!r}")


def _require_orchestrator() -> None:
    """Workers never write memories: refuse while HELIOS_BEAD is set (SPEC §13)."""
    if "HELIOS_BEAD" in os.environ:
        raise PermissionError("workers never write memories (HELIOS_BEAD is set)")


def _check_header_strings(key: str, value: Any) -> None:
    """Reject NUL and non-UTF-8-encodable strings anywhere in the header (SPEC §13).

    A header nested deep enough to exceed Python's own recursion limit
    (json's decoder and encoder tolerate far deeper) raises ValueError
    naming the key here instead of crashing (SPEC §13, decided).
    """

    def _walk(item: Any) -> None:
        if isinstance(item, str):
            if "\x00" in item:
                raise ValueError(f"memory {key!r}: header string contains NUL")
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError(
                    f"memory {key!r}: header string is not UTF-8 encodable: {exc}"
                ) from exc
        elif isinstance(item, dict):
            for entry_key, entry_value in item.items():
                if not isinstance(entry_key, str):
                    raise ValueError(
                        f"memory {key!r}: header dict key {entry_key!r} is not a string"
                    )
                _walk(entry_key)
                _walk(entry_value)
        elif isinstance(item, (list, tuple)):
            for entry in item:
                _walk(entry)

    try:
        _walk(value)
    except RecursionError as exc:
        raise ValueError(f"memory {key!r}: header is nested too deeply") from exc


def _check_body(key: str, body: str) -> None:
    """Reject an over-limit, NUL-bearing or unencodable body, naming the key (SPEC §13)."""
    if "\x00" in body:
        raise ValueError(f"memory {key!r}: body contains NUL")
    try:
        raw = body.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"memory {key!r}: body is not UTF-8 encodable: {exc}") from exc
    if len(raw) > _BODY_LIMIT_BYTES:
        raise ValueError(
            f"memory {key!r}: body is {len(raw)} bytes, over the 60000 byte limit"
        )


def _check_finite(key: str, value: Any) -> None:
    """Reject NaN and Infinity anywhere in the header, naming the key (SPEC §13).

    Catches RecursionError from a header nested deep enough to exceed
    Python's own recursion limit, raising ValueError naming the key instead
    (SPEC §13, decided).
    """

    def _walk(item: Any) -> None:
        if isinstance(item, float):
            if math.isnan(item) or math.isinf(item):
                raise ValueError(f"memory {key!r}: header has a non-finite float")
        elif isinstance(item, dict):
            for entry_key, entry_value in item.items():
                _walk(entry_key)
                _walk(entry_value)
        elif isinstance(item, (list, tuple)):
            for entry in item:
                _walk(entry)

    try:
        _walk(value)
    except RecursionError as exc:
        raise ValueError(f"memory {key!r}: header is nested too deeply") from exc


def _normalize_header(key: str, header: dict[str, Any], body: str) -> dict[str, Any]:
    """Copy the header, default a missing status to active, refuse bad ones (SPEC §13).

    Runs every refusal, including the size and encoding limits and the whole
    value cap, before the caller writes anything.
    """
    _require_orchestrator()
    _check_key(key)
    out = dict(header)
    source = out.get("source")
    if not isinstance(source, str) or not source:
        raise ValueError(f"memory {key!r}: header needs 'source' as a non-empty string")
    status = out.get("status", "active")
    if status not in _STATUSES:
        raise ValueError(f"memory {key!r}: bad status {status!r}")
    out["status"] = status
    _check_header_strings(key, out)
    _check_body(key, body)
    _check_finite(key, out)
    try:
        size = len(serialize(out, body).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"memory {key!r}: cannot serialize header: {exc}") from exc
    if size > _VALUE_LIMIT_BYTES:
        raise ValueError(
            f"memory {key!r}: value is {size} bytes, over the 65000 byte limit"
        )
    return out


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    """Write bytes via a temp file and os.replace so readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_text(key: str, path: Path) -> str:
    """Read a memory file as UTF-8 without touching line endings (SPEC §13)."""
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"memory {key!r}: file is not UTF-8: {exc}") from exc


def _exact_file(directory: Path, name: str) -> Path | None:
    """``<directory>/<name>`` only when ``name`` is exactly in the directory listing.

    A case-insensitive filesystem makes ``path.is_file()`` match a
    differently-cased name already on disk; a directory listing is the only
    way to find a key's file by its exact name (SPEC §13, decided).
    """
    try:
        entries = os.listdir(directory)
    except OSError:
        return None
    if name not in entries:
        return None
    path = directory / name
    return path if path.is_file() else None


def _bead_id_of(source: str) -> str:
    """The bead id is the source up to the first '#' (SPEC §13)."""
    return source.split("#", 1)[0]


def _is_stale(mem: Memory, labels_of: LabelsOf | None) -> bool:
    """True when the memory is active and its source bead carries truth:wrong.

    A source whose bead id does not match the bead id shape (SPEC §13,
    decided) is treated as a bead that does not exist: show is never called
    for it, since a flag-like id (``-h``, ``--json``) would make bd read it
    as an option instead of an id.
    """
    if mem.header.get("status", "active") != "active":
        return False
    source = mem.header.get("source")
    if not isinstance(source, str) or not source:
        return False
    bead_id = _bead_id_of(source)
    if not bead_id or labels_of is None:
        return False
    if re.fullmatch(_BEAD_ID_PATTERN, bead_id) is None:
        return False
    labels = labels_of(bead_id)
    return labels is not None and "truth:wrong" in labels


def _labels_from_show(show: Callable[[str], Any]) -> LabelsOf:
    """Build a label lookup from a beads show method (SPEC §13).

    A bead exists only when show returns an object whose id equals the
    requested id exactly; anything else is a missing bead. Only
    ``BeadNotFound``, the dedicated exception helios.beads raises for a
    missing bead, reads as missing; any other KeyError, or any other
    lookup error, propagates.
    """

    def lookup(bead_id: str) -> Sequence[str] | None:
        try:
            bead = show(bead_id)
        except BeadNotFound:
            return None
        if bead.id != bead_id:
            return None
        return list(bead.labels)

    return lookup


def _inject(read: Callable[[str], Memory], keys: Sequence[str]) -> str:
    """Render ``### <key>`` blocks in the given order (SPEC §13)."""
    return "\n\n".join(f"### {key}\n\n{read(key).body}" for key in keys)


class FilesBackend:
    """Memories as ``<directory>/<key>.md`` files holding exactly the value format."""

    def __init__(self, directory: Path | str, labels_of: LabelsOf | None = None) -> None:
        self.directory = Path(directory)
        self._labels_of = labels_of

    def write(self, key: str, header: dict[str, Any], body: str) -> None:
        """Validate the header, then store the value atomically (SPEC §13).

        A differently-cased name already in the directory listing (``A.md``
        for key ``a``) is refused: writing beside it would keep the old
        name, and a later exact-name read would raise KeyError (SPEC §13,
        decided).
        """
        normalized = _normalize_header(key, header, body)
        name = f"{key}.md"
        try:
            entries = os.listdir(self.directory)
        except OSError:
            entries = []
        for entry in entries:
            if entry != name and entry.lower() == name.lower():
                raise ValueError(
                    f"memory {key!r}: existing file {entry!r} collides case-insensitively"
                )
        _write_bytes_atomic(
            self.directory / name, serialize(normalized, body).encode("utf-8")
        )

    def read(self, key: str) -> Memory:
        """Parse the key file; a missing key raises KeyError (SPEC §13)."""
        _check_key(key)
        path = _exact_file(self.directory, f"{key}.md")
        if path is None:
            raise KeyError(key)
        return parse(key, _read_text(key, path))

    def stale(self) -> list[str]:
        """Active memories whose source bead carries truth:wrong, sorted by key.

        Uses the same entry listing and regular-file check as import_
        (SPEC §13, decided): a dotfile, a broken symlink, or a directory
        named like a memory file gets the same stderr skip note as a value
        that does not parse, naming the entry, and the rest are still
        checked.
        """
        found: list[str] = []
        for path in _import_entries(self.directory):
            key = path.stem
            try:
                if not path.is_file():
                    raise ValueError(f"memory {key!r}: not a regular file")
                _check_key(key)
                mem = parse(key, _read_text(key, path))
            except ValueError:
                print(
                    f"helios memory: skipping key {key!r}: not a readable memory",
                    file=sys.stderr,
                )
                continue
            if _is_stale(mem, self._labels_of):
                found.append(key)
        return sorted(found)

    def export(self, directory: Path | str) -> None:
        """Copy each memory to ``<directory>/<key>.md``; never deletes files.

        Uses the same entry listing and regular-file check as import_
        (SPEC §13, decided): every store entry is checked before any file is
        written, so a dotfile, a broken symlink, a directory named like a
        memory file, or a value that does not parse raises ValueError naming
        it and nothing is written; only the beads backend reading
        ``bd memories`` skips such values instead.
        """
        target = Path(directory)
        memories: list[Memory] = []
        for path in _import_entries(self.directory):
            key = path.stem
            if not path.is_file():
                raise ValueError(f"memory {key!r}: not a regular file")
            _check_key(key)
            memories.append(parse(key, _read_text(key, path)))
        target.mkdir(parents=True, exist_ok=True)
        for mem in memories:
            _write_bytes_atomic(
                target / f"{mem.key}.md", serialize(mem.header, mem.body).encode("utf-8")
            )

    def import_(self, directory: Path | str) -> None:
        """Read memories from only the ``*.md`` files directly in the directory.

        Every file is checked before any is written; a bad file raises
        ValueError naming it and nothing is written (SPEC §13).
        """
        _require_orchestrator()
        for key, header, body in _load_dir(Path(directory)):
            self.write(key, header, body)

    def inject(self, keys: Sequence[str]) -> str:
        """Render ``### <key>`` blocks in the given order (SPEC §13)."""
        return _inject(self.read, keys)


class BeadsBackend:
    """Memories in ``bd`` with a files export under ``memory.export_dir`` (SPEC §13)."""

    def __init__(
        self,
        beads: _BdMemories,
        export_dir: Path | str,
        labels_of: LabelsOf | None = None,
    ) -> None:
        self._beads = beads
        self.export_dir = Path(export_dir)
        self._labels_of: LabelsOf | None
        if labels_of is not None:
            self._labels_of = labels_of
        elif hasattr(beads, "show"):
            self._labels_of = _labels_from_show(getattr(beads, "show"))
        else:
            self._labels_of = None

    def write(self, key: str, header: dict[str, Any], body: str) -> None:
        """Remember in bd first, then write the export file atomically (SPEC §13)."""
        normalized = _normalize_header(key, header, body)
        value = serialize(normalized, body)
        self._beads.remember(key, value)
        _write_bytes_atomic(self.export_dir / f"{key}.md", value.encode("utf-8"))

    def read(self, key: str) -> Memory:
        """Recall from bd; a missing key raises KeyError (SPEC §13)."""
        _check_key(key)
        value = self._beads.recall(key)
        if value is None:
            raise KeyError(key)
        return parse(key, value)

    def _listed(self, note_skipped: bool) -> list[Memory]:
        """Valid memories from ``bd memories``; others are skipped, noted on stderr."""
        out: list[Memory] = []
        stored = self._beads.memories()
        for key in sorted(stored):
            value = stored[key]
            try:
                _check_key(key)
                mem = parse(key, value)
            except ValueError:
                if note_skipped:
                    print(
                        f"helios memory: skipping key {key!r}: not a readable memory",
                        file=sys.stderr,
                    )
                continue
            out.append(mem)
        return out

    def stale(self) -> list[str]:
        """Active memories whose source bead carries truth:wrong, sorted by key."""
        return sorted(
            mem.key
            for mem in self._listed(note_skipped=True)
            if _is_stale(mem, self._labels_of)
        )

    def export(self, directory: Path | str) -> None:
        """Write each bd memory to ``<directory>/<key>.md``; never deletes files."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        for mem in self._listed(note_skipped=True):
            _write_bytes_atomic(
                target / f"{mem.key}.md",
                serialize(mem.header, mem.body).encode("utf-8"),
            )

    def import_(self, directory: Path | str) -> None:
        """Remember each ``*.md`` file directly in the directory, in sorted order.

        Every file is checked before any is written; a bad file raises
        ValueError naming it and nothing is written (SPEC §13).
        """
        _require_orchestrator()
        for key, header, body in _load_dir(Path(directory)):
            self.write(key, header, body)

    def inject(self, keys: Sequence[str]) -> str:
        """Render ``### <key>`` blocks in the given order (SPEC §13)."""
        return _inject(self.read, keys)


def _import_entries(directory: Path) -> list[Path]:
    """Every directory entry whose name ends in ``.md``, dotfiles included.

    Sorted by key (stem) then file name. Nothing is filtered out ahead of
    time here: a bad name or a non-regular-file entry is caught in the check
    pass in ``_load_dir``, ``FilesBackend.export`` and ``FilesBackend.stale``,
    before anything is written (SPEC §13, decided).
    """
    if not directory.is_dir():
        return []
    return sorted(
        (path for path in directory.iterdir() if path.name.endswith(".md")),
        key=lambda p: (p.stem, p.name),
    )


def _load_dir(directory: Path) -> list[tuple[str, dict[str, Any], str]]:
    """Parse and fully validate every ``*.md`` entry directly in the directory.

    Sorted by key (stem), then file name. Every write refusal, plus the
    regular-file check (symlinks followed), runs here, so a bad entry raises
    before the caller writes any file (SPEC §13, decided).
    """
    found: list[tuple[str, dict[str, Any], str]] = []
    for path in _import_entries(directory):
        if not path.is_file():
            raise ValueError(f"memory {path.stem!r}: not a regular file")
        key = path.stem
        _check_key(key)
        mem = parse(key, _read_text(key, path))
        found.append((mem.key, _normalize_header(mem.key, mem.header, mem.body), mem.body))
    return found


def open_backend(config: Config) -> Backend:
    """Return the backend for ``memory.backend`` (SPEC §13)."""
    value = config.memory.backend
    if value == "beads":
        beads = Beads(config.hub)
        return BeadsBackend(beads, config.hub / config.memory.export_dir)
    if value == "files":
        beads = Beads(config.hub)
        return FilesBackend(
            config.hub / config.memory.export_dir,
            labels_of=_labels_from_show(beads.show),
        )
    raise ValueError(f"unknown memory backend '{value}'")
