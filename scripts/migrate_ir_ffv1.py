#!/usr/bin/env python3
"""Re-encode the infrared images of recorded Episodes from zstd to FFV1 gray, losslessly.

One-off (2026-11): infrared is FFV1 gray from now on, and the Episodes recorded before hold it
as a new recording would, as if nothing happened (no format version changes):

    .venv/bin/python scripts/migrate_ir_ffv1.py --root DATA --archive DIR --dry-run
    .venv/bin/python scripts/migrate_ir_ffv1.py --root DATA --archive DIR [--jobs N] [--only ID]

Each ``<root>/store/episodes/<b1>/<b2>/<id>.mcap`` whose camera streams (a Record stream with
fields ``infrared_left`` and ``infrared_right``) hold zstd is copied to ``<id>.mcap.tmp-migrate``
record by record, in order, chunk for chunk: the header, every schema, channel, message (its
times and sequence), metadata record and attachment as they are, except the infrared fields'
``encoding`` and ``codec_config`` in the cameras' stream descriptions (``ffv1``, the encoder's
``ffv1_config(h, w, 1)``) and those fields' bytes in each camera Record (the decoded image, encoded
by ``xpisode_format.codecs``). The copy must pass ``xpisode_format.verify`` and match the
original record for record (the infrared images decoded bit for bit) before it replaces it. The
original is then linked into ``DIR/episodes/<b1>/<b2>/<id>.mcap`` (same file system, read only)
and the copy renamed over it, read only, with the original's modification time; the sidecar gets
the new ``sha256`` and ``bytes``. Last, every run output file in JSON (a dataset's
``dataset.json``, an export's ``split.json`` or ``meta/xpisode.json``) that names a migrated
Episode by its old SHA-256 names it by the new one, as does every run record's ``read``, and the
run's record lists the rewritten files by their new SHA-256; only the hashes change, so sizes
stay. The originals of everything rewritten are kept in ``DIR`` as well. ``--relink-root R``:
the data root ``R`` (a development copy of hard links) has each migrated Episode still linked
to its original linked to the new file, with the new sidecar, and its runs updated the same way
(their originals in ``DIR/other-roots/``), so deleting ``DIR`` frees the space.

``DIR/migrate-ir-ffv1.jsonl`` records each Episode replaced (old and new SHA-256 and size): an
interrupted run is finished by running it again; a leftover ``.tmp-migrate`` is discarded. An
Episode still ``.mcap.partial``, or already FFV1, is left alone.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import dataclasses
import fcntl
import hashlib
import itertools
import json
import multiprocessing
import os
import secrets
import shutil
import sys
import time
import traceback
import zlib
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, BinaryIO

import numpy as np
import zstandard
from mcap.data_stream import RecordBuilder
from mcap.opcode import Opcode
from mcap.records import (
    Attachment,
    AttachmentIndex,
    Channel,
    Chunk,
    ChunkIndex,
    DataEnd,
    Footer,
    Header,
    McapRecord,
    Message,
    MessageIndex,
    Metadata,
    MetadataIndex,
    Schema,
    Statistics,
    SummaryOffset,
)
from mcap.stream_reader import StreamReader, breakup_chunk

from xpisode_format import _mcap, codecs, spec, wire
from xpisode_format.store import episode_paths, file_sha256, write_once
from xpisode_format.verify import verify

IR_FIELDS = ("infrared_left", "infrared_right")
TMP_SUFFIX = ".mcap.tmp-migrate"
"""Not ``.mcap.partial``: that is a recording in progress to the station."""
PARTIAL_SUFFIX = ".mcap.partial"
JOURNAL = "migrate-ir-ffv1.jsonl"
LOCK = "migrate-ir-ffv1.lock"
FREE_MARGIN = 20 << 30
"""Free bytes left over after a copy as large as its original; below that, an Episode waits."""
MAGIC = b"\x89MCAP0\r\n"


class MigrationError(Exception):
    pass


# Stream descriptions


def ir_indices(stream: spec.StreamSpec) -> tuple[int, ...]:
    """The infrared fields of a camera's Record stream; none for any other stream."""
    if not isinstance(stream, spec.RecordStream):
        return ()
    names = {f.name for f in stream.fields}
    if not set(IR_FIELDS) <= names:
        return ()
    return tuple(stream.field_index(n) for n in IR_FIELDS)


def ir_state(stream: spec.StreamSpec) -> str | None:
    """``zstd`` or ``ffv1`` for a camera stream, None for any other; a layout this tool does
    not know raises."""
    indices = ir_indices(stream)
    if not indices:
        return None
    assert isinstance(stream, spec.RecordStream)
    found = set()
    for i in indices:
        f = stream.fields[i]
        if f.dtype != "uint8" or len(f.shape) != 2:
            raise MigrationError(f"field {f.name}: {codecs.field_text(f)}, not uint8 [H, W]")
        found.add(f.encoding)
    if found == {spec.Encoding.ZSTD}:
        return "zstd"
    if found == {spec.Encoding.FFV1}:
        return "ffv1"
    raise MigrationError(f"infrared fields in {sorted(e.value for e in found)}")


def convert_description(text: str) -> str | None:
    """The camera stream description ``text`` with its infrared fields in FFV1 gray, as the
    format writes descriptions; None if there is nothing to convert. Everything else stays."""
    old = spec.RecordStream.decode(text)
    if ir_state(old) != "zstd":
        return None
    if old.model_dump_json() != text:
        raise MigrationError("a stream description does not read back as it was written")
    document = json.loads(text)
    for i in ir_indices(old):
        h, w = old.fields[i].fixed_shape
        document["fields"][i]["encoding"] = spec.Encoding.FFV1.value
        document["fields"][i]["codec_config"] = codecs.ffv1_config(h, w, channels=1)
    new = spec.RecordStream.decode(json.dumps(document))
    converted = new.model_dump_json()
    if json.loads(converted) != document:
        raise MigrationError("the converted stream description changed more than its encodings")
    return converted


def stream_text(channel: Channel) -> str:
    return channel.metadata[_mcap.STREAM_KEY]


@dataclass
class _Camera:
    old: spec.RecordStream
    new: spec.RecordStream
    indices: tuple[int, ...]


class _Channels:
    """What a file's channels are, as their records come: the cameras to convert."""

    def __init__(self) -> None:
        self.schemas: dict[int, Schema] = {}
        self.specs: dict[int, spec.StreamSpec] = {}
        self.cameras: dict[int, _Camera] = {}

    def schema(self, schema: Schema) -> None:
        self.schemas.setdefault(schema.id, schema)

    def channel(self, channel: Channel) -> Channel:
        """The channel as the copy has it."""
        if channel.id in self.specs:
            return self._converted(channel)
        stream = _mcap.channel_spec(channel, self.schemas.get(channel.schema_id))
        self.specs[channel.id] = stream
        converted = (
            convert_description(stream_text(channel))
            if isinstance(stream, spec.RecordStream)
            else None
        )
        if converted is not None:
            assert isinstance(stream, spec.RecordStream)
            new = spec.RecordStream.decode(converted)
            self.cameras[channel.id] = _Camera(stream, new, ir_indices(stream))
        return self._converted(channel)

    def _converted(self, channel: Channel) -> Channel:
        camera = self.cameras.get(channel.id)
        if camera is None:
            return channel
        metadata = dict(channel.metadata)
        metadata[_mcap.STREAM_KEY] = camera.new.model_dump_json()
        return Channel(
            id=channel.id,
            topic=channel.topic,
            message_encoding=channel.message_encoding,
            schema_id=channel.schema_id,
            metadata=metadata,
        )

    def message(self, message: Message) -> tuple[Message, int, int]:
        """The message as the copy has it, and its infrared bytes before and after."""
        camera = self.cameras.get(message.channel_id)
        if camera is None:
            return message, 0, 0
        fields, request_id = wire.decode_record(message.data)
        if wire.encode_record(fields, request_id) != message.data:
            raise MigrationError(f"a Record on channel {message.channel_id} does not re-encode")
        if len(fields) != len(camera.old.fields):
            raise MigrationError(f"a Record on channel {message.channel_id}: {len(fields)} fields")
        values = list(fields)
        before = after = 0
        for i in camera.indices:
            image = codecs.decode(camera.old.fields[i], fields[i])
            values[i] = codecs.encode(camera.new.fields[i], image)
            before, after = before + len(fields[i]), after + len(values[i])
        data = wire.encode_record(values, request_id)
        copy = Message(
            channel_id=message.channel_id,
            sequence=message.sequence,
            log_time=message.log_time,
            publish_time=message.publish_time,
            data=data,
        )
        return copy, before, after


# MCAP, written as the mcap library's Writer writes it, chunk for chunk as the original has them


def _compress(data: bytes, compression: str) -> bytes:
    if compression == "":
        return data
    if compression == "zstd":
        return zstandard.compress(data)
    if compression == "lz4":
        import lz4.frame  # pyright: ignore[reportMissingImports]

        return bytes(lz4.frame.compress(data))  # pyright: ignore[reportUnknownMemberType]
    raise MigrationError(f"chunk compression {compression!r}")


class _Writer:
    """Writes the records it is given, each chunk as one chunk, and the summary the mcap
    library writes (schemas, channels, statistics, chunk, attachment and metadata indexes,
    summary offsets; CRCs on)."""

    def __init__(self, file: BinaryIO) -> None:
        self.file = file
        self.crc = 0
        self.offset = 0
        self.schemas: dict[int, Schema] = {}
        self.channels: dict[int, Channel] = {}
        self.chunk_indexes: list[ChunkIndex] = []
        self.attachment_indexes: list[AttachmentIndex] = []
        self.metadata_indexes: list[MetadataIndex] = []
        self.stats = Statistics(
            attachment_count=0,
            channel_count=0,
            channel_message_counts=defaultdict(int),
            chunk_count=0,
            message_count=0,
            metadata_count=0,
            message_start_time=0,
            message_end_time=0,
            schema_count=0,
        )
        self._put(MAGIC)

    def _put(self, data: bytes) -> None:
        self.file.write(data)
        self.crc = zlib.crc32(data, self.crc)
        self.offset += len(data)

    def _record(self, record: McapRecord) -> tuple[int, int]:
        builder = RecordBuilder()
        record.write(builder)
        data = builder.end()
        offset = self.offset
        self._put(data)
        return offset, len(data)

    def header(self, header: Header) -> None:
        self._record(header)

    def chunk(self, records: Iterable[McapRecord], compression: str, order: list[int]) -> None:
        """One chunk, and its message indexes in ``order`` (channel ids), as the original's."""
        builder = RecordBuilder()
        indexes: dict[int, MessageIndex] = {}
        start = end = 0
        messages = 0
        for record in records:
            if isinstance(record, Schema):
                if record.id not in self.schemas:
                    self.schemas[record.id] = record
                    self.stats.schema_count += 1
            elif isinstance(record, Channel):
                if record.id not in self.channels:
                    self.channels[record.id] = record
                    self.stats.channel_count += 1
            elif isinstance(record, Message):
                start = record.log_time if messages == 0 else min(start, record.log_time)
                end = max(end, record.log_time)
                messages += 1
                indexes.setdefault(
                    record.channel_id, MessageIndex(channel_id=record.channel_id, records=[])
                ).records.append((record.log_time, builder.count))
                self._message_stats(record)
            else:
                raise MigrationError(f"a chunk holds a {type(record).__name__}")
            record.write(builder)
        data = builder.end()
        compressed = _compress(data, compression)
        chunk = Chunk(
            compression=compression,
            data=compressed,
            message_start_time=start,
            message_end_time=end,
            uncompressed_crc=zlib.crc32(data),
            uncompressed_size=len(data),
        )
        chunk_start, chunk_length = self._record(chunk)
        index_start = self.offset
        index_builder = RecordBuilder()
        offsets: dict[int, int] = {}
        if not set(indexes) <= set(order):
            raise MigrationError("the copy's message indexes are not the original's")
        for channel_id in order:
            offsets[channel_id] = index_start + index_builder.count
            indexes.get(channel_id, MessageIndex(channel_id=channel_id, records=[])).write(
                index_builder
            )
        index_data = index_builder.end()
        self._put(index_data)
        self.stats.chunk_count += 1
        self.chunk_indexes.append(
            ChunkIndex(
                message_start_time=start,
                message_end_time=end,
                chunk_start_offset=chunk_start,
                chunk_length=chunk_length,
                message_index_offsets=offsets,
                message_index_length=len(index_data),
                compression=compression,
                compressed_size=len(compressed),
                uncompressed_size=len(data),
            )
        )

    def _message_stats(self, message: Message) -> None:
        stats = self.stats
        if stats.message_count == 0:
            stats.message_start_time = message.log_time
        else:
            stats.message_start_time = min(stats.message_start_time, message.log_time)
        stats.message_end_time = max(stats.message_end_time, message.log_time)
        stats.channel_message_counts[message.channel_id] += 1
        stats.message_count += 1

    def metadata(self, metadata: Metadata) -> None:
        offset, length = self._record(metadata)
        self.stats.metadata_count += 1
        self.metadata_indexes.append(
            MetadataIndex(offset=offset, length=length, name=metadata.name)
        )

    def attachment(self, attachment: Attachment) -> None:
        offset, length = self._record(attachment)
        self.stats.attachment_count += 1
        self.attachment_indexes.append(
            AttachmentIndex(
                offset=offset,
                length=length,
                create_time=attachment.create_time,
                log_time=attachment.log_time,
                data_size=len(attachment.data),
                name=attachment.name,
                media_type=attachment.media_type,
            )
        )

    def finish(self, layout: list[McapRecord]) -> None:
        """The summary, laid out as the original's (``layout``, its summary records in order:
        the station's writer lists its channels in an order of its own)."""
        self._record(DataEnd(data_section_crc=self.crc))
        summary_start = self.offset
        chunks, attachments = iter(self.chunk_indexes), iter(self.attachment_indexes)
        metadata = iter(self.metadata_indexes)
        counts = self.stats.channel_message_counts
        records: list[McapRecord] = []
        with_offsets = False
        for record in layout:
            if isinstance(record, Schema):
                records.append(self.schemas[record.id])
            elif isinstance(record, Channel):
                records.append(self.channels[record.id])
            elif isinstance(record, Statistics):
                known = record.channel_message_counts
                order = [*known, *(c for c in counts if c not in known)]
                stats = dataclasses.replace(
                    self.stats, channel_message_counts={c: counts[c] for c in order}
                )
                records.append(stats)
            elif isinstance(record, ChunkIndex):
                records.append(next(chunks))
            elif isinstance(record, AttachmentIndex):
                records.append(next(attachments))
            elif isinstance(record, MetadataIndex):
                records.append(next(metadata))
            elif isinstance(record, SummaryOffset):
                with_offsets = True
            else:
                raise MigrationError(f"a {type(record).__name__} in the summary")
        listed = [r for r in records if isinstance(r, (Schema, Channel))]
        if (
            len(listed) != len(self.schemas) + len(self.channels)
            or next(chunks, None) is not None
            or next(attachments, None) is not None
            or next(metadata, None) is not None
        ):
            raise MigrationError("the original summary lists other records than its data")
        builder = RecordBuilder()
        offsets: list[SummaryOffset] = []
        for opcode, group in itertools.groupby(records, key=lambda r: _OPCODES[type(r)]):
            begin = builder.count
            for record in group:
                record.write(builder)
            offsets.append(
                SummaryOffset(
                    group_opcode=opcode,
                    group_start=summary_start + begin,
                    group_length=builder.count - begin,
                )
            )
        summary_offset_start = summary_start + builder.count if with_offsets else 0
        if with_offsets:
            for offset in offsets:
                offset.write(builder)
        summary = builder.end()
        crc = zlib.crc32(summary)
        crc = zlib.crc32(
            int(Opcode.FOOTER).to_bytes(1, "little")
            + (8 + 8 + 4).to_bytes(8, "little")
            + (summary_start if summary else 0).to_bytes(8, "little")
            + summary_offset_start.to_bytes(8, "little"),
            crc,
        )
        self.file.write(summary)
        footer = RecordBuilder()
        Footer(
            summary_start=summary_start if summary else 0,
            summary_offset_start=summary_offset_start,
            summary_crc=crc,
        ).write(footer)
        self.file.write(footer.end())
        self.file.write(MAGIC)


_OPCODES: dict[type, int] = {
    Schema: Opcode.SCHEMA,
    Channel: Opcode.CHANNEL,
    Statistics: Opcode.STATISTICS,
    ChunkIndex: Opcode.CHUNK_INDEX,
    AttachmentIndex: Opcode.ATTACHMENT_INDEX,
    MetadataIndex: Opcode.METADATA_INDEX,
}


@dataclass
class Rewritten:
    images: int = 0
    ir_before: int = 0
    ir_after: int = 0


def rewrite(source: Path, target: Path, *, convert: bool = True) -> Rewritten:
    """Copy the Episode ``source`` to ``target`` (which must not exist), its infrared images in
    FFV1 gray; ``convert=False`` copies it as it is (the copy is then the same bytes)."""
    with target.open("xb", buffering=8 << 20) as out:
        done = copy(source, out, convert=convert)
        out.flush()
        os.fsync(out.fileno())
    return done


def copy(source: Path, out: BinaryIO, *, convert: bool = True) -> Rewritten:
    """``rewrite`` into an open file (or anything with ``write``)."""
    channels = _Channels()
    done = Rewritten()

    def converted(record: McapRecord) -> McapRecord:
        if isinstance(record, Schema):
            channels.schema(record)
        elif isinstance(record, Channel):
            return channels.channel(record) if convert else record
        elif isinstance(record, Message) and convert:
            message, before, after = channels.message(record)
            if before:
                done.images += len(channels.cameras[record.channel_id].indices)
                done.ir_before += before
                done.ir_after += after
            return message
        return record

    with _mcap.open_bounded(source) as file:
        writer = _Writer(out)
        ended = False
        layout: list[McapRecord] = []
        pending: tuple[list[McapRecord], str] | None = None  # a chunk, until its indexes end
        order: list[int] = []
        for record in StreamReader(file, emit_chunks=True, validate_crcs=True).records:
            if isinstance(record, MessageIndex):
                order.append(record.channel_id)
                continue
            if pending is not None:
                writer.chunk(*pending, order)
                pending, order = None, []
            if ended:
                if not isinstance(record, Footer):
                    layout.append(record)
            elif isinstance(record, Header):
                writer.header(record)
            elif isinstance(record, Chunk):
                inside = breakup_chunk(record, validate_crc=True)
                pending = ([converted(r) for r in inside], record.compression)
            elif isinstance(record, Metadata):
                writer.metadata(record)
            elif isinstance(record, Attachment):
                writer.attachment(record)
            elif isinstance(record, DataEnd):
                ended = True  # the summary is made again, laid out as this one
            else:
                raise MigrationError(f"a {type(record).__name__} outside a chunk")
        if not ended:
            raise MigrationError("no MCAP data end")
        writer.finish(layout)
    return done


# Checking the copy against the original


def _records(path: Path) -> Iterator[McapRecord]:
    with _mcap.open_bounded(path) as file:
        yield from StreamReader(file, validate_crcs=True).records


def compare(original: Path, copy: Path) -> dict[str, int]:
    """Check ``copy`` against ``original`` record for record: everything equal but the cameras'
    infrared fields, whose images decode the same, bit for bit. Messages per topic."""
    channels = _Channels()
    topics: dict[int, str] = {}
    counts: dict[str, int] = defaultdict(int)
    end = object()
    pairs = itertools.zip_longest(_records(original), _records(copy), fillvalue=end)
    for n, (a, b) in enumerate(pairs):
        what = f"record {n}"
        if a is end or b is end or type(a) is not type(b):
            raise MigrationError(f"{what}: {type(a).__name__} became {type(b).__name__}")
        if isinstance(a, Schema):
            channels.schema(a)
            _same(a, b, what)
        elif isinstance(a, Channel):
            assert isinstance(b, Channel)
            _same(channels.channel(a), b, f"channel {a.topic}")
            topics[a.id] = a.topic
        elif isinstance(a, Message):
            assert isinstance(b, Message)
            _same_message(channels, a, b, f"{topics.get(a.channel_id)} @ {a.log_time}")
            counts[topics[a.channel_id]] += 1
        elif isinstance(a, MessageIndex):
            assert isinstance(b, MessageIndex)
            if a.channel_id != b.channel_id or [t for t, _ in a.records] != [
                t for t, _ in b.records
            ]:
                raise MigrationError(f"{what}: message index differs")
        elif isinstance(a, ChunkIndex):
            assert isinstance(b, ChunkIndex)
            key = ("message_start_time", "message_end_time", "compression")
            if any(getattr(a, k) != getattr(b, k) for k in key) or set(
                a.message_index_offsets
            ) != set(b.message_index_offsets):
                raise MigrationError(f"{what}: chunk index differs")
        elif isinstance(a, (AttachmentIndex, MetadataIndex)):
            skip = {"offset", "length"}
            if {k: v for k, v in vars(a).items() if k not in skip} != {
                k: v for k, v in vars(b).items() if k not in skip
            }:
                raise MigrationError(f"{what}: {type(a).__name__} differs")
        elif isinstance(a, SummaryOffset):
            assert isinstance(b, SummaryOffset)
            if a.group_opcode != b.group_opcode:
                raise MigrationError(f"{what}: summary offset differs")
        elif isinstance(a, (DataEnd, Footer)):
            pass  # their CRCs and offsets cover different bytes
        else:  # Header, Metadata, Attachment, Statistics
            _same(a, b, what)
    return dict(counts)


def _same(a: object, b: object, what: str) -> None:
    if a != b:
        raise MigrationError(f"{what} differs")


def _same_message(channels: _Channels, a: Message, b: Message, what: str) -> None:
    for key in ("channel_id", "log_time", "publish_time", "sequence"):
        if getattr(a, key) != getattr(b, key):
            raise MigrationError(f"{what}: {key} differs")
    camera = channels.cameras.get(a.channel_id)
    if camera is None:
        _same(a.data, b.data, what)
        return
    old, old_request = wire.decode_record(a.data)
    new, new_request = wire.decode_record(b.data)
    if old_request != new_request or len(old) != len(new):
        raise MigrationError(f"{what}: Record differs")
    for i, (x, y) in enumerate(zip(old, new, strict=True)):
        if i not in camera.indices:
            _same(x, y, f"{what} {camera.old.fields[i].name}")
            continue
        if camera.new.fields[i].encoding is not spec.Encoding.FFV1:
            raise MigrationError(f"{what}: {camera.new.fields[i].name} is not ffv1")
        before = codecs.decode(camera.old.fields[i], x)
        after = codecs.decode(camera.new.fields[i], y)
        if before.dtype != after.dtype or not np.array_equal(before, after):
            raise MigrationError(f"{what}: {camera.old.fields[i].name} decodes otherwise")


# One Episode


def episode_state(path: Path) -> str:
    """``zstd`` (to migrate), ``ffv1`` (done), or ``none`` (no camera with infrared)."""
    from mcap.reader import make_reader

    with _mcap.open_bounded(path) as file:
        summary = make_reader(file).get_summary()
        if summary is None:
            raise MigrationError("no MCAP summary")
        states: set[str] = set()
        for c in summary.channels.values():
            state = ir_state(_mcap.channel_spec(c, summary.schemas.get(c.schema_id)))
            if state is not None:
                states.add(state)
    if len(states) > 1:
        raise MigrationError(f"cameras in {sorted(states)}")
    return states.pop() if states else "none"


def tmp_path(path: Path) -> Path:
    return path.with_name(path.name.removesuffix(".mcap") + TMP_SUFFIX)


def archive_path(root: Path, archive: Path, path: Path) -> Path:
    return archive / path.relative_to(root / "store")


def sidecar_of(path: Path) -> Path:
    return path.with_suffix(".json")


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace_file(path: Path, data: bytes) -> None:
    """Replace ``path`` by ``data`` at once, keeping its mode and modification time."""
    status = path.stat()
    temporary = path.with_name(f"{path.name}.tmp-migrate-{secrets.token_hex(4)}")
    try:
        with temporary.open("xb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, status.st_mode & 0o7777)
        os.utime(temporary, ns=(status.st_atime_ns, status.st_mtime_ns))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    _fsync_dir(path.parent)


def keep_original(root: Path, archive: Path, path: Path) -> None:
    """A copy of the store file ``path`` in the archive, the first time only."""
    kept = archive_path(root, archive, path)
    if kept.exists():
        return
    kept.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, kept)


def _substitute(text: bytes, swaps: dict[str, str], what: str) -> bytes:
    """``text`` with each 64-hex digest in ``swaps`` replaced: the same length, nothing else
    touched."""
    out = text
    for old, new in swaps.items():
        if len(old) != len(new) or old.encode() not in out:
            raise MigrationError(f"{what}: cannot replace {old}")
        out = out.replace(old.encode(), new.encode())
    return out


def update_sidecar(
    root: Path, archive: Path, path: Path, old_sha: str, new_sha: str, new_bytes: int
) -> str:
    """Give the sidecar the new file's ``sha256`` and ``bytes``; ``updated``, ``current`` or
    ``none`` (no sidecar)."""
    sidecar = sidecar_of(path)
    if not sidecar.is_file():
        return "none"
    text = sidecar.read_bytes()
    document = json.loads(text)
    if document.get("sha256") == new_sha and document.get("bytes") == new_bytes:
        return "current"
    if document.get("sha256") != old_sha:
        raise MigrationError(f"{sidecar.name} names {document.get('sha256')}, not {old_sha}")
    expected = {**document, "sha256": new_sha, "bytes": new_bytes}
    old_bytes = document["bytes"]
    fragment = f'"sha256":"{old_sha}","bytes":{old_bytes},'.encode()
    if text.count(fragment) == 1:
        data = text.replace(fragment, f'"sha256":"{new_sha}","bytes":{new_bytes},'.encode())
    else:  # written otherwise than sidecar.ensure writes: written again, as ensure would
        data = json.dumps(expected, ensure_ascii=False, separators=(",", ":")).encode()
    if json.loads(data) != expected:
        raise MigrationError(f"{sidecar.name}: the rewrite would change more than sha256, bytes")
    keep_original(root, archive, sidecar)
    replace_file(sidecar, data)
    return "updated"


@dataclass
class Result:
    episode_id: str
    state: str
    """migrated, ffv1, none, partial, error, dry."""
    bytes_before: int = 0
    bytes_after: int = 0
    images: int = 0
    ir_before: int = 0
    ir_after: int = 0
    seconds: float = 0.0
    sidecar: str = ""
    detail: str = ""


def _journal(archive: Path) -> Path:
    return archive / JOURNAL


def append_journal(archive: Path, entry: dict[str, Any]) -> None:
    line = (json.dumps(entry, separators=(",", ":")) + "\n").encode()
    with _journal(archive).open("ab") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        file.write(line)
        file.flush()
        os.fsync(file.fileno())


def _entries(archive: Path) -> Iterator[dict[str, Any]]:
    if _journal(archive).is_file():
        for line in _journal(archive).read_text().splitlines():
            if line.strip():
                yield json.loads(line)


def read_journal(archive: Path) -> dict[str, dict[str, Any]]:
    """Per Episode id, its last entry."""
    return {e["episode_id"]: e for e in _entries(archive) if "episode_id" in e}


def rewritten_files(archive: Path) -> dict[str, str]:
    """Each version of a dataset.json this tool replaced, by SHA-256: the one it became."""
    return {e["old_sha256"]: e["new_sha256"] for e in _entries(archive) if "file" in e}


def migrate_episode(root: Path, archive: Path, path: Path, dry_run: bool = False) -> Result:
    """Migrate one Episode; an Episode that fails is left as it was."""
    started = time.monotonic()
    episode_id = path.name.removesuffix(".mcap")
    result = Result(episode_id, "error")
    tmp = tmp_path(path)
    try:
        if path.with_name(episode_id + PARTIAL_SUFFIX).exists() or not path.is_file():
            result.state = "partial"
            return result
        if not dry_run:
            tmp.unlink(missing_ok=True)  # an interrupted run's
        status = path.stat()
        result.bytes_before = result.bytes_after = status.st_size
        state = episode_state(path)
        if state != "zstd":
            result.state = state
            if state == "ffv1" and not dry_run:
                result.sidecar = _finish_sidecar(root, archive, path)
            return result
        if dry_run:
            result.state = "dry"
            return result
        free = shutil.disk_usage(path.parent).free
        if free < status.st_size + FREE_MARGIN:
            raise MigrationError(f"{free >> 30} GiB free: too little for a copy")
        old_sha = file_sha256(path)
        sidecar = sidecar_of(path)
        if sidecar.is_file():
            named = json.loads(sidecar.read_bytes()).get("sha256")
            if named != old_sha:
                raise MigrationError(f"the file's SHA-256 is {old_sha}, its sidecar's {named}")
        done = rewrite(path, tmp)
        report = verify(tmp)
        if not report.ok:
            raise MigrationError("verify: " + "; ".join(report.problems[:5]))
        compare(path, tmp)
        new_sha = file_sha256(tmp)
        new_bytes = tmp.stat().st_size
        # Replace: the original into the archive (a second link, so the store keeps a
        # <id>.mcap all along), then the copy over it.
        kept = archive_path(root, archive, path)
        kept.parent.mkdir(parents=True, exist_ok=True)
        if kept.exists():
            if not kept.samefile(path):
                raise MigrationError(f"{kept} exists and is another file")
        else:
            os.link(path, kept)
        os.chmod(kept, 0o444)
        _fsync_dir(kept.parent)
        append_journal(
            archive,
            {
                "episode_id": episode_id,
                "path": path.relative_to(root / "store").as_posix(),
                "old_sha256": old_sha,
                "old_bytes": status.st_size,
                "new_sha256": new_sha,
                "new_bytes": new_bytes,
                "images": done.images,
                "unix_ns": time.time_ns(),
            },
        )
        os.chmod(tmp, 0o444)
        os.utime(tmp, ns=(status.st_atime_ns, status.st_mtime_ns))
        os.rename(tmp, path)
        _fsync_dir(path.parent)
        result.sidecar = update_sidecar(root, archive, path, old_sha, new_sha, new_bytes)
        result.state = "migrated"
        result.bytes_after = new_bytes
        result.images, result.ir_before, result.ir_after = (
            done.images,
            done.ir_before,
            done.ir_after,
        )
    except Exception as error:  # noqa: BLE001 - one Episode's failure is reported, not fatal
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        result.state = "error"
        result.detail = f"{type(error).__name__}: {error}"
        if not isinstance(error, MigrationError):
            result.detail += "\n" + traceback.format_exc()
    finally:
        result.seconds = time.monotonic() - started
    return result


def _finish_sidecar(root: Path, archive: Path, path: Path) -> str:
    """An Episode replaced by an earlier, interrupted run: its sidecar, if not yet updated."""
    entry = read_journal(archive).get(path.name.removesuffix(".mcap"))
    if entry is None or path.stat().st_size != entry["new_bytes"]:
        return ""
    return update_sidecar(
        root, archive, path, entry["old_sha256"], entry["new_sha256"], entry["new_bytes"]
    )


# Run outputs (datasets, exports) and run records


SMALL = 16 << 20
"""Run output files at most this large, JSON or JSON lines, are searched for old hashes."""
JSON_SUFFIXES = (".json", ".jsonl")
TEXT_SUFFIXES = (".txt", ".csv", ".yaml", ".yml", ".toml", ".md")
"""Searched too, but only told of: this tool rewrites JSON only."""


def _run_records(root: Path) -> Iterator[Path]:
    yield from sorted((root / "store" / "runs").glob("*/*/*.json"))


def migrated(root: Path, archive: Path) -> dict[str, str]:
    """Old SHA-256 to new, of each Episode replaced whose file in the store is the new one."""
    swaps: dict[str, str] = {}
    for entry in read_journal(archive).values():
        path = root / "store" / entry["path"]
        if path.is_file() and path.stat().st_size == entry["new_bytes"]:
            swaps[entry["old_sha256"]] = entry["new_sha256"]
    return swaps


def _parsed(data: bytes, suffix: str) -> Any:
    text = data.decode()
    if suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def _swapped(value: Any, swaps: dict[str, str]) -> Any:
    """``value`` with every old hash in its strings (keys too) replaced by the new."""
    if isinstance(value, str):
        for old, new in swaps.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_swapped(v, swaps) for v in value]
    if isinstance(value, dict):
        return {_swapped(k, swaps): _swapped(v, swaps) for k, v in value.items()}
    return value


@dataclass
class Runs:
    changed: list[str] = field(default_factory=list[str])
    """What was (or would be) rewritten, as ``path: what``."""
    untouched: list[str] = field(default_factory=list[str])
    """Other files that name migrated Episodes by their old SHA-256."""


def update_runs(root: Path, archive: Path, swaps: dict[str, str], dry_run: bool = False) -> Runs:
    """Every run's output file (a dataset, an export's metadata: small JSON) that names a
    migrated Episode by its old SHA-256 names it by the new one, and so does every record's
    ``read``; a record lists its rewritten files by their new SHA-256. Only hashes change:
    each file keeps its size and every other byte."""
    found = Runs()
    store = root / "store"
    for record_path in _run_records(root):
        record_text = record_path.read_bytes()
        record = json.loads(record_text)
        folder = record_path.with_suffix("")
        expected_record = json.loads(record_text)
        record_swaps: dict[str, str] = {}
        rewriting: set[str] = set()
        # The run's own files.
        for path in sorted(folder.rglob("*")) if swaps and folder.is_dir() else ():
            if not path.is_file() or ".tmp-" in path.name or path.stat().st_size > SMALL:
                continue
            if path.suffix not in JSON_SUFFIXES + TEXT_SUFFIXES:
                continue
            text = path.read_bytes()
            hits = {old: new for old, new in swaps.items() if old.encode() in text}
            if not hits:
                continue
            where = path.relative_to(store).as_posix()
            if path.suffix in TEXT_SUFFIXES:
                found.untouched.append(f"{where}: {len(hits)} old Episode sha256")
                continue
            data = _substitute(text, hits, where)
            try:
                same = _parsed(data, path.suffix) == _swapped(_parsed(text, path.suffix), hits)
            except ValueError:
                same = False
            if not same:
                raise MigrationError(f"{where}: the old hashes are not only in its strings")
            found.changed.append(f"{where}: {len(hits)} old Episode sha256")
            rewriting.add(path.relative_to(folder).as_posix())
            if not dry_run:
                keep_original(root, archive, path)
                before, after = hashlib.sha256(text).hexdigest(), hashlib.sha256(data).hexdigest()
                append_journal(
                    archive,
                    {
                        "file": where,
                        "old_sha256": before,
                        "new_sha256": after,
                        "unix_ns": time.time_ns(),
                    },
                )
                replace_file(path, data)
        # The record's facts of the files rewritten, now or by a run of this tool cut short.
        versions = rewritten_files(archive)
        ours = rewritten_paths(archive)
        for name, fact in record.get("files", {}).items():
            target = folder / name
            if dry_run:
                if name in rewriting:
                    record_swaps[fact["sha256"]] = "0" * 64
                continue
            if target.relative_to(store).as_posix() not in ours or not target.is_file():
                continue
            now = file_sha256(target)
            if fact["sha256"] == now:
                continue
            version = fact["sha256"]
            for _ in range(len(versions)):
                if version == now or version not in versions:
                    break
                version = versions[version]
            if version != now:
                raise MigrationError(f"{record_path}: {name} is not as listed")
            if target.stat().st_size != fact["bytes"]:
                raise MigrationError(f"{record_path}: {name} changed size")
            record_swaps[fact["sha256"]] = now
            expected_record["files"][name]["sha256"] = now
        # What the run read, by hash.
        for reference in record.get("read", ()):
            digest = reference.removeprefix("sha256:")
            if reference.startswith("sha256:") and digest in swaps:
                record_swaps[digest] = swaps[digest]
        if "read" in record:
            expected_record["read"] = [
                f"sha256:{swaps[r[7:]]}" if r.startswith("sha256:") and r[7:] in swaps else r
                for r in record.get("read", ())
            ]
        if record_swaps:
            found.changed.append(
                f"{record_path.relative_to(store)}: {len(record_swaps)} sha256 references"
            )
            if not dry_run:
                data = _substitute(record_text, record_swaps, record_path.name)
                if json.loads(data) != expected_record:
                    raise MigrationError(f"{record_path}: the hashes appear where they should not")
                keep_original(root, archive, record_path)
                replace_file(record_path, data)
    return found


def rewritten_paths(archive: Path) -> set[str]:
    return {e["file"] for e in _entries(archive) if "file" in e}


# Other data roots holding the same files


def relink(root: Path, archive: Path, other: Path) -> tuple[list[str], dict[str, str]]:
    """In the data root ``other`` (a development copy made of hard links), each migrated
    Episode still linked to its original is linked to the new file instead, and gets the new
    sidecar; anything else there is left alone. What was done, and the hash swaps that now
    hold in ``other``."""
    lines: list[str] = []
    swaps: dict[str, str] = {}
    for entry in read_journal(archive).values():
        new = root / "store" / entry["path"]
        theirs = other / "store" / entry["path"]
        kept = archive / entry["path"]
        if not new.is_file() or new.stat().st_size != entry["new_bytes"] or not theirs.exists():
            continue
        if not theirs.samefile(new):
            if not (kept.exists() and theirs.samefile(kept)):
                lines.append(f"{theirs}: not the original's link, left alone")
                continue
            lines.append(f"{theirs}: linked to the new file")
            link = theirs.with_name(theirs.name + ".tmp-migrate-link")
            link.unlink(missing_ok=True)
            os.link(new, link)
            os.rename(link, theirs)
            _fsync_dir(theirs.parent)
        swaps[entry["old_sha256"]] = entry["new_sha256"]
        sidecar, their_sidecar = sidecar_of(new), sidecar_of(theirs)
        if not sidecar.is_file():
            continue
        data = sidecar.read_bytes()
        if their_sidecar.is_file():
            if their_sidecar.read_bytes() == data:
                continue
            named = json.loads(their_sidecar.read_bytes()).get("sha256")
            if named not in (entry["old_sha256"], entry["new_sha256"]):
                lines.append(f"{their_sidecar}: names {named}, left alone")
                continue
            replace_file(their_sidecar, data)
        else:
            write_once(their_sidecar, data)
        lines.append(f"{their_sidecar}: the new sidecar")
    return lines, swaps


# The command


def _gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB"


def _line(k: int, total: int, r: Result) -> str:
    head = f"[{k}/{total}] {r.episode_id}"
    if r.state == "migrated":
        per = (
            f", infrared {r.ir_before / r.images / 1e3:.0f} -> {r.ir_after / r.images / 1e3:.0f}"
            " KB/image"
            if r.images
            else ""
        )
        return (
            f"{head} migrated {_gb(r.bytes_before)} -> {_gb(r.bytes_after)}"
            f"{per}, {r.images} images, {r.seconds:.1f} s"
        )
    if r.state == "dry":
        return f"{head} would migrate ({_gb(r.bytes_before)})"
    if r.state == "error":
        return f"{head} FAILED, left as it was: {r.detail}"
    if r.state == "ffv1":
        fixed = f", sidecar {r.sidecar}" if r.sidecar in ("updated",) else ""
        return f"{head} already ffv1{fixed}"
    if r.state == "partial":
        return f"{head} skipped: still recording (.mcap.partial) or gone"
    return f"{head} skipped: no camera with infrared"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--root", required=True, type=Path, help="the data root (store/ inside)")
    parser.add_argument(
        "--archive",
        type=Path,
        help="where the originals go, on the store's file system (required unless --dry-run)",
    )
    parser.add_argument("--dry-run", action="store_true", help="say what would change")
    parser.add_argument(
        "--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2), help="Episodes at once"
    )
    parser.add_argument("--only", metavar="ID", help="this Episode only")
    parser.add_argument(
        "--relink-root",
        action="append",
        default=[],
        type=Path,
        metavar="DIR",
        help="another data root of hard links to this store's files: relink it to the new ones",
    )
    args = parser.parse_args(argv)
    root: Path = args.root.expanduser().absolute()
    if not (root / "store" / "episodes").is_dir():
        parser.error(f"{root} has no store/episodes")
    if args.archive is None and not args.dry_run:
        parser.error("--archive is required")
    archive: Path = (args.archive or root / "nowhere").expanduser().absolute()
    lock: IO[bytes] | None = None
    if not args.dry_run:
        archive.mkdir(parents=True, exist_ok=True)
        if archive.stat().st_dev != (root / "store" / "episodes").stat().st_dev:
            parser.error(f"{archive} is on another file system than {root}/store")
        if archive.resolve().is_relative_to((root / "store").resolve()):
            parser.error("the archive cannot be inside the store")
        lock = (archive / LOCK).open("ab")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error(f"another migration holds {archive / LOCK}")
    try:
        paths = [
            p for p in episode_paths(root) if args.only in (None, p.name.removesuffix(".mcap"))
        ]
        if args.only and not paths:
            parser.error(f"no Episode {args.only}")
        paths.sort(key=lambda p: -p.stat().st_size)  # the largest first: they finish together
        started = time.monotonic()
        results: list[Result] = []
        total = len(paths)
        jobs = 1 if args.dry_run else max(1, args.jobs)
        if jobs == 1:
            for path in paths:
                results.append(migrate_episode(root, archive, path, args.dry_run))
                print(_line(len(results), total, results[-1]), flush=True)
        else:
            context = multiprocessing.get_context("spawn")
            with concurrent.futures.ProcessPoolExecutor(jobs, mp_context=context) as pool:
                futures = [pool.submit(migrate_episode, root, archive, p) for p in paths]
                for future in concurrent.futures.as_completed(futures):
                    results.append(future.result())
                    print(_line(len(results), total, results[-1]), flush=True)
        swaps = (
            migrated(root, archive)
            if not args.dry_run
            else _would_swap(root, [r for r in results if r.state == "dry"])
        )
        runs = update_runs(root, archive, swaps, args.dry_run)
        for other in args.relink_root:
            other = other.expanduser().absolute()
            lines, theirs = (
                _would_relink(root, other, [r for r in results if r.state == "dry"])
                if args.dry_run
                else relink(root, archive, other)
            )
            for line in lines:
                print(("would relink " if args.dry_run else "relinked ") + line)
            # Its own datasets and records, their originals kept apart in the archive.
            kept = archive / "other-roots" / str(other).strip("/").replace("/", "_")
            if not args.dry_run:
                kept.mkdir(parents=True, exist_ok=True)
            more = update_runs(other, kept, theirs, args.dry_run)
            runs.changed += [f"{other}: {line}" for line in more.changed]
            runs.untouched += [f"{other}: {line}" for line in more.untouched]
        for line in runs.changed:
            print(("would rewrite " if args.dry_run else "rewrote ") + line)
        for line in runs.untouched:
            print(f"not changed (not JSON): {line}")
        by_state: dict[str, list[Result]] = defaultdict(list)
        for r in results:
            by_state[r.state].append(r)
        done = by_state["migrated"] + by_state["dry"]
        before = sum(r.bytes_before for r in done)
        after = sum(r.bytes_after for r in by_state["migrated"])
        seconds = time.monotonic() - started
        counts = ", ".join(f"{len(v)} {k}" for k, v in sorted(by_state.items()))
        print(f"{total} Episodes: {counts}")
        if args.dry_run:
            print(f"would migrate {len(done)} Episodes, {_gb(before)}")
        else:
            print(
                f"migrated {len(done)} Episodes: {_gb(before)} -> {_gb(after)}"
                f" ({_gb(before - after)} less), {seconds:.0f} s"
                f" ({seconds / len(done) if done else 0:.1f} s per Episode with {jobs} jobs)"
            )
    finally:
        if lock is not None:
            lock.close()
    return 1 if by_state["error"] else 0


def _would_relink(root: Path, other: Path, would: list[Result]) -> tuple[list[str], dict[str, str]]:
    """In a dry run: which of the Episodes that would be migrated ``other`` links to."""
    lines: list[str] = []
    swaps: dict[str, str] = {}
    for r in would:
        path = next(root.glob(f"store/episodes/*/*/{r.episode_id}.mcap"), None)
        if path is None:
            continue
        theirs = other / path.relative_to(root)
        if theirs.exists() and theirs.samefile(path):
            lines.append(f"{theirs}: linked to the new file")
            if sidecar_of(path).is_file():
                swaps[json.loads(sidecar_of(path).read_bytes())["sha256"]] = "f" * 64
    return lines, swaps


def _would_swap(root: Path, would: list[Result]) -> dict[str, str]:
    """In a dry run: the sidecars' hashes of the Episodes that would be migrated."""
    swaps: dict[str, str] = {}
    for r in would:
        path = next(root.glob(f"store/episodes/*/*/{r.episode_id}.mcap"), None)
        if path is not None and sidecar_of(path).is_file():
            swaps[json.loads(sidecar_of(path).read_bytes())["sha256"]] = "f" * 64
    return swaps


if __name__ == "__main__":
    sys.exit(main())
