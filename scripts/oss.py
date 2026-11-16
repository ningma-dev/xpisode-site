#!/usr/bin/env python3
"""Sync the data root's store with the central OSS, and free local disk of what OSS holds.

Run it through scripts/oss.sh, on any machine (it needs this repo's .venv):

    scripts/oss.sh status                             what is not on OSS yet, what waits,
                                                      how long the disk and the backlog last,
                                                      and what OSS holds (by its index)
    scripts/oss.sh push                               upload what may go up and OSS lacks,
                                                      newest Episodes first
    scripts/oss.sh push --dataset ID                  a dataset first: its members, their
                                                      judgments and plans, then its record;
                                                      then stop (with --follow: go on)
    scripts/oss.sh push --run ID                      one run (an export, a training run), the
                                                      run files it read and their records, no
                                                      recordings; then stop
    scripts/oss.sh push --follow                      keep pushing as the panel sees writes,
                                                      newest first, and evict when under
                                                      100 GB are free
    scripts/oss.sh pull                               bring down the small things OSS has
    scripts/oss.sh pull --dataset ID [--link --mount /mnt/data_oss/xpisode_data]
                                                      and the recordings a dataset needs
    scripts/oss.sh pull --run ID                      a run's files (a checkpoint) and the
                                                      run files it read; --with-episodes too
                                                      the recordings it read
    scripts/oss.sh delete ID --reason TEXT --admin-config PATH
                                                      delete an uploaded Episode everywhere
    scripts/oss.sh evict [--days 7] [--free-gb N] [--yes]
                                                      delete recordings here that OSS holds

One topology (design 7.6): every machine talks only to the central OSS. Small things
(judgments, plans, tasks, people, run records, sidecars, thumbnails, a run's dataset.json and
calibration.json, robot models) go up and come down; big things (recordings, other run
files) only go up, and come down on demand (``pull --dataset``, ``pull --run``).

The store (docs/reference/store.md in the xpisode repo) maps one to one onto the remote prefix:
``<root>/store/episodes/3f/9a/<id>.mcap`` is ``<remote>episodes/3f/9a/<id>.mcap``. The
training servers mount the bucket read only, so /mnt/data_oss/xpisode_data reads there as a
data root; ``pull --link`` links big files to it instead of copying them.

The index (store.md 5): every push that uploads anything also uploads one segment,
``index/<segment id>.jsonl.zst``, a line for each sidecar, run record, judgment, plan
revision and person it put on OSS (the file's own JSON). ``pull`` and ``status`` read the
segments (listing only ``index/``, fetching only those not seen before, kept in
``local/sync/index/``), so nobody lists the whole bucket; only while OSS has no segment at
all do they list the prefix. Tasks (TOML) and robot models are not records: their two small
folders are listed.

Every store file is written once (store.md 2.2): nothing is overwritten either way, and a key
that exists on both sides with two sizes (or, for a record, two contents) is a conflict that
stops everything.

Going up (store.md 7): an Episode only once ``xpisode_format.sync.pushable`` (a plan's
recording once judged, any other 10 minutes after it was sealed), its ``.mcap`` before its
sidecar and thumbnail; run records after their files. Each key is marked ``uploading`` in
the upload ledger (``local/sync/ledger.jsonl``) under its lock before it is sent and
``pushed`` once OSS holds it at its size, so deleting at the source never removes what may
have left. Keys pulled from OSS are marked ``pushed`` too.

ossutil (2.x) does the transfers with its own CRC64 check; its credentials are in
~/.ossutilconfig (mode 600) or the admin's config, never here. ``status --here`` never
calls it.
"""

from __future__ import annotations

import argparse
import calendar
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import zstandard

from xpisode_format.deletions import deleted, record_deletion
from xpisode_format.documents import Termination
from xpisode_format.ids import check_id, new_id
from xpisode_format.judgments import JudgmentReader
from xpisode_format.library import Library
from xpisode_format.names import check_who
from xpisode_format.runs import RunReader, RunRecord, parse_episode, record_path, write_record
from xpisode_format.sync import Ledger, episode_keys, ledger_lock, pushable, sealed

REMOTE = os.environ.get("XPISODE_OSS", "oss://shanghai-ai-prod-oss/xpisode_data/store/")
OSSUTIL = os.environ.get(
    "OSSUTIL", shutil.which("ossutil") or str(Path.home() / ".local/bin/ossutil")
)
PANEL = os.environ.get("XPISODE_PANEL", "http://127.0.0.1:8043")
MOUNT = Path("/mnt/data_oss/xpisode_data")
"""Where the training servers mount the bucket's ``xpisode_data/`` (read only)."""
SPEED_MB_S = 20.0
"""Upload speed from the station host to oss-cn-shanghai, measured 2026-11-11 with a 2 GB
file: 13 MB/s at 16 parts in flight, 17.5 at 32, 20 at 64, 20 at 128 (the link is full)."""
PARALLEL = 64
"""Parts of one file in flight at once (ossutil ``--parallel``): a recording is one file,
and one connection carries well under 1 MB/s, so it takes many at once to fill the link."""
PART_SIZE = "16Mi"
JOBS = 4
"""Files in flight at once (ossutil ``-j``)."""
CHUNK_BYTES = 2 * 10**9
"""A push goes Episode by Episode in chunks of about this much, with a progress line each."""
SMALL_RUN_BYTES = 64 * 2**20
"""A run whose files to send are no more than this goes up with the small things."""
SEGMENT_EVERY_S = 600.0
"""A long push writes an index segment at least this often, not only at its end."""
INDEX_MARGIN_S = 3600.0
"""Listing ``index/`` starts this long before the newest segment seen (clocks differ)."""
INDEX_FULL_S = 86400.0
"""All of ``index/`` is listed again this often (a segment uploaded long after it was made)."""
LIST_THREADS = 16
ID = r"\d{8}T\d{6}Z-[0-9a-f]{12}"
RUN_RECORD = re.compile(rf"^runs/[0-9a-f]{{2}}/[0-9a-f]{{2}}/({ID})\.json$")
RUN_OUTPUT = re.compile(rf"^runs/([0-9a-f]{{2}}/[0-9a-f]{{2}}/({ID}))/(.+)$")
EPISODE_FILE = re.compile(rf"^episodes/[0-9a-f]{{2}}/[0-9a-f]{{2}}/({ID})\.(mcap|json|jpg)$")
RUN_REFERENCE = re.compile(rf"^run:({ID})(?:/(.+))?$")
SEGMENT_KEY = re.compile(rf"^index/({ID})\.jsonl\.zst$")
SMALL_FOLDERS = ("judgments", "plans", "tasks", "people", "models")
LISTED_FOLDERS = ("tasks", "models")
"""Small things that are not JSON records, so not in the index: their folders are listed."""
SMALL_OUTPUTS = ("dataset.json", "calibration.json")
"""A run's own files that are small things: what datasets and calibration corrections are."""
WHO = "oss.py"
"""The ``who`` of this tool's own run records, unless XPISODE_WHO names a person."""

FREE_AFTER_S = 600.0
FOLLOW_SCAN_S = 60.0
FOLLOW_RECONCILE_S = 6 * 3600.0
FOLLOW_RECORD_S = 3600.0
FOLLOW_EVICT_S = 600.0
FOLLOW_RETRY_S = 30.0
"""After a failed upload, ``follow`` waits this long, doubling each time it fails again."""
FOLLOW_RETRY_MAX_S = 600.0
EVICT_BELOW_GB = 100.0
"""``push --follow`` evicts when the disk has less free than this (``--evict-below``)."""
WAIT_S = 30.0  # the panel's longest wait on /api/v1/writes


def data_root(start: Path) -> Path:
    """The ``[tool.xpisode] data`` of the nearest pyproject.toml that has it."""
    for directory in (start, *start.parents):
        project = directory / "pyproject.toml"
        if project.is_file():
            settings = tomllib.loads(project.read_text()).get("tool", {}).get("xpisode")
            if settings is not None:
                return (directory / Path(settings.get("data", "data")).expanduser()).resolve()
    raise SystemExit("no pyproject.toml with [tool.xpisode] above here; pass --root")


def bucketed(kind: str, ident: str) -> str:
    random = ident.rsplit("-", 1)[1]
    return f"{kind}/{random[0:2]}/{random[2:4]}/{ident}"


def program_who() -> str:
    try:
        return check_who(os.environ.get("XPISODE_WHO", ""))
    except ValueError:
        return WHO


@dataclass
class Local:
    key: str
    path: Path
    bytes: int


def ignored(key: str) -> bool:
    name = key.rsplit("/", 1)[-1]
    return name.endswith(".partial") or ".tmp-" in name


def episode_file(key: str) -> tuple[str, str] | None:
    """``(episode_id, suffix)`` of a recording, sidecar or thumbnail key."""
    match = EPISODE_FILE.match(key)
    return (match.group(1), match.group(2)) if match else None


def thumbnail(key: str) -> bool:
    episode = episode_file(key)
    return episode is not None and episode[1] == "jpg"


def recording_key(key: str) -> str | None:
    """The recording's key for an Episode file's key."""
    episode = episode_file(key)
    return episode_keys(episode[0])[0] if episode is not None else None


def small(key: str) -> bool:
    """Whether the key is a small thing, which comes down with every ``pull``."""
    top = key.split("/", 1)[0]
    if top in SMALL_FOLDERS:
        return True
    if top == "episodes":
        return key.endswith((".json", ".jpg"))
    if RUN_RECORD.match(key):
        return True
    output = RUN_OUTPUT.match(key)
    return output is not None and output.group(3) in SMALL_OUTPUTS


def local_files(store: Path) -> list[Local]:
    """What may go up: every finished file, outputs of finished runs only, records last."""
    found: list[Local] = []
    for directory, _, names in os.walk(store):
        for name in names:
            path = Path(directory, name)
            key = path.relative_to(store).as_posix()
            if ignored(key):
                continue
            output = RUN_OUTPUT.match(key)
            if output and not (store / "runs" / f"{output.group(1)}.json").is_file():
                continue  # the run has not ended: its outputs do not exist yet
            try:
                size = path.stat().st_size
            except OSError:
                continue  # a link to a mount that is not there
            found.append(Local(key, path, size))
    found.sort(key=lambda f: (RUN_RECORD.match(f.key) is not None, f.key))
    return found


# -- ossutil ---------------------------------------------------------------------------------


@dataclass
class Transfer:
    """How ossutil moves files: parts of one file at once, part size, files at once."""

    parallel: int = PARALLEL
    part_size: str = PART_SIZE
    jobs: int = JOBS

    def options(self) -> list[str]:
        return [
            "--parallel",
            str(self.parallel),
            "--part-size",
            self.part_size,
            "-j",
            str(self.jobs),
            "--no-progress",
        ]


TRANSFER = Transfer()


class TransferError(SystemExit):
    """ossutil failed: the network, DNS, OSS itself. ``push --follow`` waits and tries again;
    a one-off command stops, as before."""


def ossutil(*args: str, config: Path | None = None, capture: bool = True) -> str:
    if not Path(OSSUTIL).exists():
        raise SystemExit(f"ossutil not found at {OSSUTIL}")
    command = [OSSUTIL, *args]
    if config is not None:
        command += ["--config-file", str(config)]
    done = subprocess.run(command, capture_output=capture, text=True)
    if done.returncode != 0:
        raise TransferError(f"ossutil {' '.join(args[:2])} failed:\n{done.stderr or done.stdout}")
    return done.stdout if capture else ""


def bucket_key(remote: str, key: str) -> str:
    """The object's name in its bucket, for ``--start-after``."""
    return (remote + key).removeprefix("oss://").split("/", 1)[1]


def remote_files(
    remote: str, prefix: str = "", *, config: Path | None = None, start_after: str | None = None
) -> dict[str, int]:
    """Key (relative to the remote prefix) to size, for every object under ``prefix``
    (after the key ``start_after``, if given). Folder markers (``a/``) are left out."""
    extra = ["--start-after", bucket_key(remote, start_after)] if start_after else []
    listing = ossutil("ls", "-r", remote + prefix, *extra, config=config)
    sizes: dict[str, int] = {}
    for line in listing.splitlines():
        parts = line.split()
        # LastModifiedTime (four words), Size, StorageClass, ETAG, ObjectName
        if len(parts) >= 8 and parts[-1].startswith(remote) and parts[-4].isdigit():
            key = parts[-1][len(remote) :]
            if key and not key.endswith("/"):
                sizes[key] = int(parts[-4])
    return sizes


def listing_prefix(key: str) -> str:
    """The narrowest prefix to list to find ``key``: an Episode's three files share
    ``episodes/3f/9a/<id>.``, anything else its folder."""
    episode = episode_file(key)
    if episode is not None:
        return key[: -len(episode[1])]
    return key.rsplit("/", 1)[0] + "/" if "/" in key else ""


def remote_sizes(remote: str, keys: Iterable[str], *, config: Path | None = None) -> dict[str, int]:
    """The sizes on OSS of ``keys`` (those absent are left out), each found by listing its
    own narrow prefix, several at once; never the whole bucket."""
    wanted = set(keys)
    if not wanted:
        return {}
    prefixes = sorted({listing_prefix(k) for k in wanted})
    with ThreadPoolExecutor(max_workers=min(LIST_THREADS, len(prefixes))) as pool:
        listed = pool.map(lambda p: remote_files(remote, p, config=config), prefixes)
        there: dict[str, int] = {}
        for found in listed:
            there.update(found)
    return {k: s for k, s in there.items() if k in wanted}


def copy_listed(source: str, target: str, keys: list[str], *, config: Path | None = None) -> None:
    """``ossutil cp -r`` of ``keys``, relative to ``source``, into ``target``; up, an object
    already there is left as it is (its size is checked after)."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as listing:
        listing.write("\n".join(keys) + "\n")
    up = target.startswith("oss://")
    try:
        ossutil(
            "cp",
            "-r",
            source,
            target,
            "--files-from",
            listing.name,
            *TRANSFER.options(),
            *(["--ignore-existing"] if up else []),
            config=config,
        )  # its own report is kept back: the push's progress lines say it
    finally:
        os.unlink(listing.name)


# -- index segments (store.md 5) -------------------------------------------------------------


def document_keys(document: object) -> tuple[tuple[str, str], dict[str, int | None]] | None:
    """What one index line says: its identity, by which readers deduplicate, and the keys
    it shows OSS holds, with their sizes where it says them (None: not said). A sidecar
    shows its recording (which goes up first) and itself; a run record itself and its
    files. None for a line of a kind this tool does not know (store.md 6: ignored)."""
    if not isinstance(document, dict):
        return None
    kind = document.get("format")
    try:
        if kind == "xpisode.sidecar/1":
            episode_id = check_id(document["episode"]["episode_id"])
            mcap, sidecar, _ = episode_keys(episode_id)
            return ("episode", episode_id), {mcap: int(document["bytes"]), sidecar: None}
        if kind == "xpisode.run/1":
            run = check_id(document["run"])
            base = bucketed("runs", run)
            keys: dict[str, int | None] = {f"{base}.json": None}
            for path, fact in dict(document.get("files", {})).items():
                keys[f"{base}/{path}"] = int(fact["bytes"])
            return ("run", run), keys
        if kind == "xpisode.judgment/1":
            judgment = check_id(document["judgment"])
            episode_id = parse_episode(document["about"])[0]
            return ("judgment", judgment), {
                f"{bucketed('judgments', episode_id)}/{judgment}.json": None
            }
        if kind == "xpisode.plan/1":
            plan, revision = check_id(document["plan"]), check_id(document["revision"])
            return ("plan", revision), {f"{bucketed('plans', plan)}/{revision}.json": None}
        if kind == "xpisode.person/1":
            person = check_id(document["person"])
            return ("person", person), {f"people/{person}.json": None}
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return None


def segment_line(path: Path) -> bytes | None:
    """The file's JSON as one line of a segment, if it is a record the index holds."""
    if path.suffix != ".json":
        return None
    try:
        document = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    if document_keys(document) is None:
        return None
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()


def write_segment(root: Path, lines: Iterable[bytes]) -> Path:
    """``store/index/<segment id>.jsonl.zst`` of ``lines``, written once."""
    data = zstandard.ZstdCompressor(level=10).compress(b"".join(line + b"\n" for line in lines))
    folder = root / "store" / "index"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{new_id()}.jsonl.zst"
    temporary = folder / f"{path.name}.tmp-{secrets.token_hex(4)}"
    with open(temporary, "wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    os.rename(temporary, path)
    return path


def read_segment(data: bytes) -> list[dict[str, Any]]:
    """A segment's lines; those that do not parse are left out."""
    raw = zstandard.ZstdDecompressor().decompressobj().decompress(data)
    found: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            document = json.loads(line)
        except ValueError:
            continue
        if isinstance(document, dict):
            found.append(document)
    return found


@dataclass
class RemoteIndex:
    """What the remote's index segments say OSS holds, deduplicated."""

    documents: dict[tuple[str, str], dict[str, Any]] = field(
        default_factory=dict[tuple[str, str], dict[str, Any]]
    )
    segments: int = 0
    disagreements: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    """Identities seen with two contents: a record rewritten (store.md 2.2)."""

    def add(self, documents: Iterable[dict[str, Any]]) -> None:
        for document in documents:
            shown = document_keys(document)
            if shown is None:
                continue
            identity = shown[0]
            before = self.documents.setdefault(identity, document)
            if before != document:
                self.disagreements.append(identity)

    def keys(self) -> dict[str, int | None]:
        """Each key OSS holds by the index, with its size where a line says it."""
        found: dict[str, int | None] = {}
        for document in self.documents.values():
            shown = document_keys(document)
            if shown is not None:
                for key, size in shown[1].items():
                    if found.get(key) is None:
                        found[key] = size
        return found

    def record_of(self) -> dict[str, dict[str, Any]]:
        """Key of each record file to its JSON."""
        found: dict[str, dict[str, Any]] = {}
        for document in self.documents.values():
            shown = document_keys(document)
            if shown is not None:
                for key, size in shown[1].items():
                    if size is None:
                        found[key] = document
        return found

    def deleted(self) -> set[str]:
        """Episodes the deletion records in the index delete."""
        gone: set[str] = set()
        for (kind, _), document in self.documents.items():
            if kind == "run":
                for ref in document.get("deletes", ()):
                    try:
                        gone.add(parse_episode(str(ref))[0])
                    except ValueError:
                        continue
        return gone

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [d for (k, _), d in self.documents.items() if k == kind]


def index_cache(root: Path, remote: str) -> Path:
    """``local/sync/index/<remote's hash>/``: the segments of that remote seen here."""
    digest = hashlib.sha256(remote.encode()).hexdigest()[:16]
    return root / "local" / "sync" / "index" / digest


def id_unix_s(ident: str) -> float:
    return float(calendar.timegm(time.strptime(ident.split("-", 1)[0], "%Y%m%dT%H%M%SZ")))


def id_at(unix_s: float) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(max(unix_s, 0.0)))


def remote_index(
    root: Path, remote: str, *, config: Path | None = None, now_s: float | None = None
) -> RemoteIndex | None:
    """The remote's index: its segments not seen here are listed (from shortly before the
    newest seen; all of ``index/`` once a day) and fetched into ``local/sync/index/``, then
    every segment seen is read. None while OSS has no segment."""
    cache = index_cache(root, remote)
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "remote.txt").write_text(remote + "\n")
    now = now_s if now_s is not None else time.time()
    seen = {m.group(1) for p in cache.iterdir() if (m := SEGMENT_KEY.match(f"index/{p.name}"))}
    state_path = cache / "listed.json"
    try:
        full_at = float(json.loads(state_path.read_text())["full_unix_s"])
    except (OSError, ValueError, KeyError, TypeError):
        full_at = 0.0
    after = None
    if seen and now - full_at < INDEX_FULL_S:
        after = f"index/{id_at(id_unix_s(max(seen)) - INDEX_MARGIN_S)}"
    listed = remote_files(remote, "index/", config=config, start_after=after)
    if after is None:
        state_path.write_text(json.dumps({"full_unix_s": now}) + "\n")
    new = {
        k: s for k, s in listed.items() if (m := SEGMENT_KEY.match(k)) and m.group(1) not in seen
    }
    if new:
        staging = cache / f"incoming-{secrets.token_hex(4)}"
        try:
            copy_listed(remote, f"{staging}/", sorted(new), config=config)
            for key, size in new.items():
                staged = staging / key
                if staged.is_file() and staged.stat().st_size == size:
                    os.replace(staged, cache / staged.name)
                else:
                    print(f"index segment {key} did not come down whole; next time")
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    paths = sorted(cache.glob("*.jsonl.zst"))
    if not paths:
        return None
    index = RemoteIndex(segments=len(paths))
    for path in paths:
        try:
            index.add(read_segment(path.read_bytes()))
        except (OSError, zstandard.ZstdError) as error:
            print(f"index segment {path.name} unreadable here: {error}")
    for kind, ident in index.disagreements[:5]:
        print(f"index: two different {kind} records {ident} on OSS (a store file rewritten)")
    return index


# -- push ------------------------------------------------------------------------------------


def make_sidecars(root: Path) -> None:
    """Each recording's sidecar and thumbnail, made where missing, so they go up after it
    (store.md 4)."""
    from xpisode_format.sidecar import ensure

    gone = deleted(root)
    for path in sorted((root / "store" / "episodes").glob("*/*/*.mcap")):
        if path.stem in gone or path.is_symlink():
            continue
        if not path.with_suffix(".json").exists() or not path.with_suffix(".jpg").exists():
            try:
                ensure(path)
            except Exception as error:  # noqa: BLE001 - one bad file must not stop the rest
                print(f"no sidecar for {path.name}: {error}")


def is_pushed(ledger: Ledger, key: str, size: int) -> bool:
    return ledger.state(key) == "pushed" and ledger.bytes(key) == size


@dataclass
class Backlog:
    """What of this store is not on OSS yet, by this host's ledger."""

    ready: list[Local] = field(default_factory=list[Local])
    """May go up now."""
    waiting: list[Local] = field(default_factory=list[Local])
    """Files of Episodes that may not go up yet (a plan's recording not judged, a free one
    not 10 minutes old)."""

    @property
    def unpushed_bytes(self) -> int:
        return sum(f.bytes for f in self.ready) + self.waiting_bytes

    @property
    def waiting_bytes(self) -> int:
        return sum(f.bytes for f in self.waiting)

    @property
    def waiting_episodes(self) -> int:
        return len({e[0] for f in self.waiting if (e := episode_file(f.key)) is not None})


def backlog(
    root: Path,
    *,
    now_unix_ns: int,
    free_after_s: float = FREE_AFTER_S,
    files: list[Local] | None = None,
) -> Backlog:
    """Every unpushed file here, split into what may go up now and what waits. A deleted
    Episode's files never go up; a sidecar or thumbnail only with or after its recording."""
    ledger = Ledger(root)
    gone = deleted(root)
    allowed: dict[str, bool] = {}
    found = Backlog()
    for f in files if files is not None else local_files(root / "store"):
        if is_pushed(ledger, f.key, f.bytes):
            continue
        episode = episode_file(f.key)
        if episode is None:
            found.ready.append(f)
            continue
        episode_id = episode[0]
        if episode_id in gone:
            continue
        if episode_id not in allowed:
            allowed[episode_id] = ledger.state(episode_keys(episode_id)[0]) == "pushed" or (
                pushable(root, episode_id, now_unix_ns=now_unix_ns, free_after_s=free_after_s)
            )
        (found.ready if allowed[episode_id] else found.waiting).append(f)
    return found


def rank(key: str) -> int:
    """Upload order within a run's or an Episode's files: small records first, then
    recordings and run files, then sidecars and thumbnails (a sidecar on OSS means its
    recording is there), run records last."""
    if RUN_RECORD.match(key):
        return 3
    episode = episode_file(key)
    if episode is not None:
        return 1 if episode[1] == "mcap" else 2
    return 1 if key.startswith("runs/") else 0


def run_of(key: str) -> str | None:
    """The run whose record or file ``key`` is."""
    if match := RUN_RECORD.match(key):
        return match.group(1)
    if match := RUN_OUTPUT.match(key):
        return match.group(2)
    return None


Chunk = list[list[Local]]
"""Batches sent one after another; a chunk's keys are checked on OSS together."""


def plan_push(
    ready: list[Local], *, last_runs: set[str] | None = None, chunk_bytes: int = CHUNK_BYTES
) -> list[Chunk]:
    """The order of a push: the small things; runs with little to send (records after
    their files); Episodes newest first, about ``chunk_bytes`` a chunk (recordings, then
    sidecars and thumbnails); then the other runs, one a chunk: those with much to send,
    those that read an Episode of this push (a dataset of new recordings), and
    ``last_runs``."""
    last = last_runs or set()
    plain: list[Local] = []
    runs: dict[str, list[Local]] = {}
    episodes: dict[str, list[Local]] = {}
    for f in ready:
        if (run := run_of(f.key)) is not None:
            runs.setdefault(run, []).append(f)
        elif (episode := episode_file(f.key)) is not None:
            episodes.setdefault(episode[0], []).append(f)
        else:
            plain.append(f)
    chunks: list[Chunk] = []
    if plain:
        chunks.append([sorted(plain, key=lambda f: f.key)])

    def run_chunk(files: list[Local]) -> Chunk:
        outputs = sorted((f for f in files if not RUN_RECORD.match(f.key)), key=lambda f: f.key)
        records = [f for f in files if RUN_RECORD.match(f.key)]
        return [b for b in (outputs, records) if b]

    late = last | {r for r, files in runs.items() if reads_any(files, set(episodes))}
    small_runs = [
        r
        for r in sorted(runs)
        if r not in late
        and sum(f.bytes for f in runs[r] if not RUN_RECORD.match(f.key)) <= SMALL_RUN_BYTES
    ]
    if small_runs:
        files = [f for r in small_runs for f in runs[r]]
        chunks.append(run_chunk(files))
    current: list[Local] = []
    size = 0
    for episode_id in sorted(episodes, reverse=True):  # ids begin with their UTC second
        files = episodes[episode_id]
        current += files
        size += sum(f.bytes for f in files)
        if size >= chunk_bytes:
            chunks.append(episode_chunk(current))
            current, size = [], 0
    if current:
        chunks.append(episode_chunk(current))
    for run in sorted(set(runs) - set(small_runs)):
        chunks.append(run_chunk(runs[run]))
    return chunks


def reads_any(files: list[Local], episodes: set[str]) -> bool:
    """Whether the run whose files these are read one of ``episodes`` (a dataset of them,
    a check of them): then it goes up after them."""
    for f in files:
        if RUN_RECORD.match(f.key):
            try:
                reads = json.loads(f.path.read_bytes()).get("read", [])
            except (OSError, ValueError, AttributeError):
                return False
            for ref in reads:
                try:
                    if str(ref).startswith("episode:") and parse_episode(str(ref))[0] in episodes:
                        return True
                except ValueError:
                    continue
    return False


def episode_chunk(files: list[Local]) -> Chunk:
    recordings = [f for f in files if rank(f.key) == 1]
    after = [f for f in files if rank(f.key) == 2]
    return [b for b in (recordings, after) if b]


def upload(store: Path, remote: str, batch: list[Local], config: Path | None = None) -> None:
    copy_listed(f"{store}/", remote, [f.key for f in batch], config=config)


def send(
    root: Path, remote: str, batch: list[Local], ledger: Ledger, config: Path | None = None
) -> list[Local]:
    """Upload ``batch``: under the ledger's lock, the files still here are marked
    ``uploading`` (a deleter may have removed some just before); then they are sent, their
    sizes on OSS checked, and they are marked ``pushed``. What was sent."""
    if not batch:
        return []
    with ledger_lock(root):
        here: list[Local] = []
        for f in batch:
            try:
                size = f.path.stat().st_size
            except FileNotFoundError:
                continue  # deleted at the source meanwhile
            if size != f.bytes:
                raise SystemExit(f"{f.key} changed size: a store file is written once")
            here.append(f)
        ledger.mark(((f.key, f.bytes) for f in here), "uploading")
    if not here:
        return []
    upload(root / "store", remote, here, config)
    there = remote_sizes(remote, (f.key for f in here), config=config)
    wrong = [f.key for f in here if there.get(f.key) != f.bytes]
    if wrong:
        raise SystemExit(
            f"after upload, OSS does not hold these as they are here: {wrong[:5]}"
            " (one there before with another size is a conflict: a store file rewritten)"
        )
    ledger.mark(((f.key, f.bytes) for f in here), "pushed")
    return here


def conflict(key: str, there: int | str, here: int | str, remote: str) -> SystemExit:
    return SystemExit(
        f"conflict: {remote}{key} is {there} on OSS, {here} here. A store file is written"
        " once; one of the two was rewritten. Nothing more was copied."
    )


def owed_path(root: Path) -> Path:
    return root / "local" / "sync" / "owed.jsonl"


def owe(root: Path, files: Iterable[Local]) -> None:
    """Note the index lines of ``files`` in ``local/sync/owed.jsonl`` before they are
    marked ``pushed``: a push killed before its segment went up leaves them there, and the
    next push puts them in its segment (``Segment``). Without this, a record sent by a push
    that never published its segment would be on OSS but in no segment, so invisible to
    every ``pull``."""
    entries = []
    for f in files:
        line = segment_line(f.path)
        if line is not None:
            entries.append(json.dumps({"key": f.key, "line": line.decode()}, ensure_ascii=False))
    if not entries:
        return
    path = owed_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_lock(root), open(path, "a", encoding="utf-8") as file:
        file.write("".join(e + "\n" for e in entries))
        file.flush()
        os.fsync(file.fileno())


def owed(root: Path) -> dict[str, bytes]:
    """The owed lines, by key."""
    found: dict[str, bytes] = {}
    try:
        text = owed_path(root).read_text(encoding="utf-8")
    except OSError:
        return found
    for raw in text.splitlines():
        try:
            entry = json.loads(raw)
            found[str(entry["key"])] = str(entry["line"]).encode()
        except (ValueError, KeyError, TypeError):
            continue  # a line cut short by a crash
    return found


def settle(root: Path, keys: set[str]) -> None:
    """Strike ``keys`` from the owed lines: a segment that names them is on OSS."""
    path = owed_path(root)
    if not keys or not path.exists():
        return
    with ledger_lock(root):
        kept = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                if str(json.loads(raw)["key"]) in keys:
                    continue
            except (ValueError, KeyError, TypeError):
                continue
            kept.append(raw + "\n")
        temporary = path.with_name(f"{path.name}.tmp-{secrets.token_hex(4)}")
        temporary.write_text("".join(kept), encoding="utf-8")
        os.replace(temporary, path)


def unindexed(root: Path, remote: str) -> list[Local]:
    """Records this host pushed (by its ledger) that no index segment names: sent by a push
    that died before its segment, before ``owe`` existed. ``follow`` owes them again."""
    index = remote_index(root, remote)
    shown = index.keys() if index is not None else {}
    ledger = Ledger(root)
    return [
        f
        for f in local_files(root / "store")
        if f.key not in shown
        and is_pushed(ledger, f.key, f.bytes)
        and segment_line(f.path) is not None
    ]


class Segment:
    """The lines of the index segment a push owes: one per record it put on OSS, and those
    an earlier push owed and did not publish (``owe``)."""

    def __init__(self, root: Path | None = None) -> None:
        self.lines: dict[str, bytes] = {}
        self.written = time.monotonic()
        if root is not None:
            ledger = Ledger(root)
            self.lines.update((k, v) for k, v in owed(root).items() if ledger.state(k) == "pushed")

    def add(self, files: Iterable[Local]) -> None:
        for f in files:
            line = segment_line(f.path)
            if line is not None:
                self.lines[f.key] = line

    def due(self) -> bool:
        return bool(self.lines) and time.monotonic() - self.written >= SEGMENT_EVERY_S

    def publish(self, root: Path, remote: str, config: Path | None = None) -> Path | None:
        """Write the segment into ``store/index/`` and send it; the lines are then owed no
        more. A segment that did not go up goes with the next push."""
        self.written = time.monotonic()
        if not self.lines:
            return None
        path = write_segment(root, self.lines.values())
        settle(root, set(self.lines))  # the segment file here owes them now: it goes up
        self.lines = {}
        own = Local(path.relative_to(root / "store").as_posix(), path, path.stat().st_size)
        send(root, remote, [own], Ledger(root), config)
        return path


@dataclass
class Progress:
    """Files and bytes of a push, done and to do, and how fast."""

    files: int
    bytes: int
    started: float = field(default_factory=time.monotonic)
    done_files: int = 0
    done_bytes: int = 0
    sent_bytes: int = 0

    def add(self, files: Iterable[Local], *, sent: bool) -> None:
        for f in files:
            self.done_files += 1
            self.done_bytes += f.bytes
            if sent:
                self.sent_bytes += f.bytes

    def rate(self) -> float:
        """Bytes a second sent so far."""
        return self.sent_bytes / max(time.monotonic() - self.started, 1e-3)

    def line(self) -> str:
        rate = self.rate()
        left = max(self.bytes - self.done_bytes, 0)
        eta = "-" if rate <= 0 else duration(left / rate)
        return (
            f"{self.done_files}/{self.files} files, {self.done_bytes / 1e9:.2f}/"
            f"{self.bytes / 1e9:.2f} GB, {rate / 1e6:.1f} MB/s, ETA {eta}"
        )


def duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
    return f"{seconds // 60}m{seconds % 60:02d}s"


def dataset_scope(root: Path, dataset_id: str) -> tuple[set[str], set[str]]:
    """What ``push --dataset`` sends, as keys: the dataset run's record and files; its
    members' recordings, sidecars and thumbnails; their judgments and the revisions of the
    plans they were recorded for; every task, person and robot model (few, small). And the
    members."""
    dataset = Library(root).dataset(dataset_id)
    record = RunReader(root).get(dataset_id)
    if dataset is None or record is None:
        raise SystemExit(f"no dataset {dataset_id} here")
    store = root / "store"
    base = bucketed("runs", dataset_id)
    keys = {f"{base}.json", *(f"{base}/{path}" for path in record.files)}
    members = {e.episode_id for e in dataset.episodes}
    plans: set[str] = set()
    for episode_id in members:
        keys.update(episode_keys(episode_id))
        for path in (store / bucketed("judgments", episode_id)).glob("*.json"):
            keys.add(path.relative_to(store).as_posix())
        try:
            sidecar = json.loads((store / episode_keys(episode_id)[1]).read_bytes())
            plans.add(check_id(sidecar["episode"]["context"]["collection"]["plan"]))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    for plan in plans:
        for path in (store / bucketed("plans", plan)).glob("*.json"):
            keys.add(path.relative_to(store).as_posix())
    for top in ("tasks", "people", "models"):
        for path in (store / top).rglob("*"):
            if path.is_file():
                keys.add(path.relative_to(store).as_posix())
    return keys, members


def run_scope(root: Path, run_id: str) -> set[str]:
    """What ``push --run`` sends, as keys: the run's record and files, the run files it read
    (``run:<id>/<path>``) through the runs it read and what they read in turn, the records
    of all those runs, and every task, person and robot model. No recordings: a training
    server reads the run's output (an export), not the Episodes behind it."""
    reader = RunReader(root)
    if reader.get(run_id) is None:
        raise SystemExit(f"no run {run_id} here")
    store = root / "store"
    keys = set(needs(root, run_id, episodes=False).files)
    seen: set[str] = set()
    stack = [run_id]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        record = reader.get(current)
        if record is None:
            continue
        keys.add(f"{bucketed('runs', current)}.json")
        for ref in record.read:
            if match := RUN_REFERENCE.match(ref):
                stack.append(match.group(1))
    base = bucketed("runs", run_id)
    keys.update(f"{base}/{path}" for path in reader.get(run_id).files)  # type: ignore[union-attr]
    for top in ("tasks", "people", "models"):
        for path in (store / top).rglob("*"):
            if path.is_file():
                keys.add(path.relative_to(store).as_posix())
    return keys


def push(
    root: Path,
    remote: str,
    *,
    now_unix_ns: int | None = None,
    free_after_s: float = FREE_AFTER_S,
    record: bool = True,
    quiet: bool = False,
    dataset: str | None = None,
    run: str | None = None,
    budget_s: float | None = None,
    chunk_bytes: int = CHUNK_BYTES,
) -> list[Local]:
    """Upload what may go up and OSS lacks, in ``plan_push`` order, with a progress line a
    chunk and an index segment for the records it put on OSS; what was uploaded. With
    ``dataset``, only what that dataset needs, its record last; with ``run``, only that run,
    the run files it read and their records (``run_scope``), its record last. With
    ``budget_s``, no new chunk is begun after that long (``follow`` then looks for newer
    recordings). With
    ``record``, a push that uploads anything leaves a run record of its own, which goes up
    too."""
    started = time.time_ns()
    now = now_unix_ns if now_unix_ns is not None else started
    make_sidecars(root)
    ledger = Ledger(root)
    left = backlog(root, now_unix_ns=now, free_after_s=free_after_s)
    ready = left.ready
    last: set[str] = set()
    if dataset is not None:
        scope, members = dataset_scope(root, dataset)
        ready = [f for f in ready if f.key in scope]
        last = {dataset}
        waiting = {e[0] for f in left.waiting if (e := episode_file(f.key)) and f.key in scope}
        absent = {
            m
            for m in members
            if not (root / "store" / episode_keys(m)[0]).exists()
            and ledger.state(episode_keys(m)[0]) != "pushed"
        }
        if waiting:
            print(f"dataset {dataset}: {len(waiting)} members may not go up yet (not judged)")
        if absent:
            print(f"dataset {dataset}: {len(absent)} members are not here, e.g. {min(absent)}")
    if run is not None:
        scope = run_scope(root, run)
        ready = [f for f in ready if f.key in scope]
        last = {run}
        missing = sorted(k for k in scope if not (root / "store" / k).exists() and "/" in k)
        if missing:
            print(f"run {run}: {len(missing)} files it needs are not here, e.g. {missing[0]}")
    segment = Segment(root)
    if not ready:
        segment.publish(root, remote)  # what an earlier push owed, if anything
        if not quiet:
            print("OSS has everything that may go up.")
        return []
    chunks = plan_push(ready, last_runs=last, chunk_bytes=chunk_bytes)
    progress = Progress(len(ready), sum(f.bytes for f in ready))
    uploaded: list[Local] = []
    announced = False
    clock = time.monotonic()
    for chunk in chunks:
        if budget_s is not None and uploaded and time.monotonic() - clock >= budget_s:
            break
        files = [f for batch in chunk for f in batch]
        there = remote_sizes(remote, (f.key for f in files))
        already: list[Local] = []
        for f in files:
            size = there.get(f.key)
            if size is not None and size != f.bytes:
                raise conflict(f.key, f"{size} bytes", f"{f.bytes} bytes", remote)
            if size == f.bytes:
                already.append(f)
        if already:
            owe(root, already)
            with ledger_lock(root):
                ledger.mark(((f.key, f.bytes) for f in already if f.path.exists()), "pushed")
            segment.add(already)
            progress.add(already, sent=False)
        present = {f.key for f in already}
        for batch in chunk:
            batch = [f for f in batch if f.key not in present]
            if batch and rank(batch[0].key) == 2:  # a sidecar only once its recording is there
                batch = [f for f in batch if ledger.state(recording_key(f.key) or "") == "pushed"]
            if not batch:
                continue
            if not announced:
                print(
                    f"uploading up to {progress.files} files, {progress.bytes / 1e9:.2f} GB,"
                    f" to {remote}"
                )
                announced = True
            owe(root, batch)
            sent = send(root, remote, batch, ledger)
            uploaded += sent
            segment.add(sent)
            progress.add(sent, sent=True)
        if announced:
            print(f"  {progress.line()}", flush=True)
        if segment.due():
            segment.publish(root, remote)
    if record and uploaded:
        segment.add([record_push(root, remote, uploaded, started, dataset=dataset, run=run)])
    segment.publish(root, remote)
    if uploaded:
        seconds = (time.time_ns() - started) / 1e9
        size = sum(f.bytes for f in uploaded)
        print(
            f"done: {len(uploaded)} files, {size / 1e9:.2f} GB in {duration(seconds)}"
            f" ({size / 1e6 / max(seconds, 1e-3):.1f} MB/s)"
        )
    return uploaded


def reference(key: str, remote: str) -> str:
    return remote.rstrip("/") + "/" + key


def record_push(
    root: Path,
    remote: str,
    uploaded: list[Local],
    started: int,
    *,
    dataset: str | None = None,
    run: str | None = None,
) -> Local:
    """The push's run record, written and sent now (or the next push would send it and
    record that too); the caller puts it in its index segment."""
    size = sum(f.bytes for f in uploaded)
    record = RunRecord(
        run=new_id(),
        who=program_who(),
        via="cli",
        started_unix_ns=started,
        ended_unix_ns=time.time_ns(),
        termination=Termination.FINISHED,
        wrote=tuple(dict.fromkeys(reference(f.key, remote) for f in uploaded)),
        host=socket.gethostname(),
        command=(
            "scripts/oss.py",
            "push",
            *(("--dataset", dataset) if dataset else ()),
            *(("--run", run) if run else ()),
        ),
        note=f"uploaded {len(uploaded)} files, {size} bytes",
    )
    path = write_record(root, record)
    own = Local(path.relative_to(root / "store").as_posix(), path, path.stat().st_size)
    owe(root, [own])
    send(root, remote, [own], Ledger(root))
    print(f"recorded as {path.name}")
    return own


class Writes:
    """The panel's ``GET /api/v1/writes``, waited on: something new arrived in the store."""

    def __init__(self, panel: str) -> None:
        self.panel = panel.rstrip("/")
        self.after: int | None = None
        self.epoch: str | None = None
        self.reachable = True

    def _page(self, after: int, wait_s: float) -> dict[str, Any]:
        query = urllib.parse.urlencode({"after": after, "limit": 1000, "wait_s": f"{wait_s:.1f}"})
        url = f"{self.panel}/api/v1/writes?{query}"
        with urllib.request.urlopen(url, timeout=wait_s + 10) as response:
            return json.loads(response.read())

    def _head(self) -> None:
        after = 0
        while True:
            page = self._page(after, 0)
            after = int(page["next"])
            if not page["writes"]:
                break
        self.after, self.epoch = after, str(page["epoch"])

    def wait(self, seconds: float, sleep: Callable[[float], None] = time.sleep) -> bool:
        """Whether something was written within ``seconds``; without the panel, a sleep."""
        deadline = time.monotonic() + seconds
        try:
            if self.after is None:
                self._head()
            while (left := deadline - time.monotonic()) > 0:
                page = self._page(self.after or 0, min(left, WAIT_S))
                if str(page["epoch"]) != self.epoch:
                    self.after = None  # the panel rebuilt its index: from the newest again
                    return True
                self.after = int(page["next"])
                if page["writes"]:
                    return True
            if not self.reachable:
                print(f"panel at {self.panel} again: pushing on its writes")
            self.reachable = True
            return False
        except (OSError, ValueError, KeyError, TypeError):  # URLError is an OSError
            if self.reachable:
                print(f"no panel at {self.panel}: looking every {seconds:.0f} s")
            self.reachable, self.after = False, None
            sleep(max(deadline - time.monotonic(), 0.0))
            return False


def follow(
    root: Path,
    remote: str,
    panel: str,
    *,
    scan_s: float = FOLLOW_SCAN_S,
    reconcile_s: float = FOLLOW_RECONCILE_S,
    record_s: float = FOLLOW_RECORD_S,
    evict_below: int | None = None,
    rounds: int | None = None,
    budget_s: float = SEGMENT_EVERY_S,
) -> None:
    """Push, newest Episodes first, for at most ``budget_s`` a round, so that a recording
    sealed meanwhile goes before the older backlog; after a round that sent nothing, wait
    for a write (at most ``scan_s``: the time rules). Pull the small things at start and
    every ``reconcile_s``. One run record per ``record_s`` of uploads, and one at the end.
    With ``evict_below``, whenever the disk has fewer free bytes, ``evict`` (7 days, as
    much as is missing; at most every 10 minutes). ``rounds``: stop after that many."""
    writes = Writes(panel)
    uploaded: list[Local] = []
    started = time.time_ns()
    reconciled: float | None = None
    evicted: float | None = None
    done = 0

    def record() -> None:
        segment = Segment(root)
        segment.add([record_push(root, remote, uploaded, started)])
        segment.publish(root, remote)

    failures = 0
    try:
        while rounds is None or done < rounds:
            try:
                if reconciled is None or time.monotonic() - reconciled >= reconcile_s:
                    pull(root, remote)
                    if missing := unindexed(root, remote):
                        print(f"{len(missing)} records on OSS are in no index segment: indexing")
                        owe(root, missing)
                    reconciled = time.monotonic()
                sent = push(root, remote, record=False, quiet=True, budget_s=budget_s)
            except TransferError as error:
                failures += 1
                pause = min(FOLLOW_RETRY_S * 2 ** (failures - 1), FOLLOW_RETRY_MAX_S)
                first = str(error).splitlines()[0]
                print(f"upload failed ({first}); trying again in {pause:.0f} s", flush=True)
                reconciled = None  # look at OSS afresh: what went up before the failure
                done += 1
                retry_sleep(pause)
                continue
            if failures:
                print(f"uploading again after {failures} failed attempts", flush=True)
                failures = 0
            uploaded += sent
            if uploaded and (time.time_ns() - started) / 1e9 >= record_s:
                record()
                uploaded, started = [], time.time_ns()
            free = shutil.disk_usage(root).free
            due = evicted is None or time.monotonic() - evicted >= FOLLOW_EVICT_S
            if evict_below is not None and free < evict_below and due:
                evict(root, remote, free_bytes=evict_below - free, yes=True)
                evicted = time.monotonic()
            done += 1
            if (rounds is None or done < rounds) and not sent:
                writes.wait(scan_s)
    finally:
        if uploaded:
            try:
                record()
            except TransferError as error:
                print(f"the push's run record waits for the next push: {error}", flush=True)


def retry_sleep(seconds: float) -> None:
    """How ``follow`` waits after a failed upload (tests replace it)."""
    time.sleep(seconds)


# -- pull ------------------------------------------------------------------------------------


def place(staged: Path, final: Path, size: int, remote: str) -> None:
    """Put a downloaded file at its store path, never over another (write once)."""
    final.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(staged, final)
        return
    except FileExistsError:
        pass
    except OSError:  # another file system: copy beside it, then link
        temporary = final.with_name(f"{final.name}.tmp-{secrets.token_hex(4)}")
        shutil.copyfile(staged, temporary)
        try:
            os.link(temporary, final)
            return
        except FileExistsError:
            pass
        finally:
            temporary.unlink()
    here = final.stat().st_size
    if here != size:
        raise conflict(final.name, f"{size} bytes", f"{here} bytes", remote)


def same_record(path: Path, document: dict[str, Any]) -> bool:
    try:
        return json.loads(path.read_bytes()) == document
    except (OSError, ValueError):
        return False


def download(
    root: Path,
    remote: str,
    keys: list[str],
    sizes: dict[str, int | None],
    config: Path | None = None,
    *,
    records: dict[str, dict[str, Any]] | None = None,
    optional: set[str] | None = None,
) -> list[str]:
    """Copy ``keys`` from OSS into the store, files before run records, each marked
    ``pushed`` (it is on OSS); what was copied. A size None is not known beforehand: then
    a record's content is checked against ``records`` (its index line). A key in
    ``optional`` may be absent; any other absent is reported and left out."""
    if not keys:
        return []
    known = [sizes[k] for k in keys if sizes.get(k) is not None]
    total = sum(s for s in known if s is not None)
    print(f"downloading {len(keys)} files, {total / 1e9:.2f} GB")
    ledger = Ledger(root)
    staging = root / "local" / "sync" / f"incoming-{secrets.token_hex(4)}"
    copied: list[str] = []
    absent: list[str] = []
    try:
        for last in (False, True):
            batch = [k for k in keys if (RUN_RECORD.match(k) is not None) == last]
            if not batch:
                continue
            staging.mkdir(parents=True, exist_ok=True)
            copy_listed(remote, f"{staging}/", batch, config=config)
            landed: list[tuple[str, int]] = []
            for key in batch:
                staged = staging / key
                if not staged.is_file():
                    if key not in (optional or set()):
                        absent.append(key)
                    continue
                size, expected = staged.stat().st_size, sizes.get(key)
                if expected is not None and size != expected:
                    raise SystemExit(f"{remote}{key} did not come down whole; nothing placed")
                document = (records or {}).get(key)
                if expected is None and document is not None and not same_record(staged, document):
                    raise conflict(key, "one record", "another in the index", remote)
                landed.append((key, size))
            for key, size in landed:
                place(staging / key, root / "store" / key, size, remote)
            ledger.mark(landed, "pushed")
            copied += [k for k, _ in landed]
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if absent:
        print(f"not on OSS though listed: {len(absent)} files, e.g. {absent[0]}")
    return copied


def remove_episode(root: Path, episode_id: str) -> bool:
    """Remove this machine's copies of a deleted Episode (thumbnail, sidecar, recording);
    whether there were any. A link is removed, never what it points to."""
    removed = False
    with ledger_lock(root):
        for key in reversed(episode_keys(episode_id)):
            path = root / "store" / key
            if path.is_symlink() or path.exists():
                path.unlink()
                removed = True
    return removed


def apply_deletions(root: Path) -> list[str]:
    """Remove the local copies of every Episode a deletion record deletes."""
    return [e for e in sorted(deleted(root)) if remove_episode(root, e)]


@dataclass
class Remote:
    """What OSS holds, as ``pull`` and ``status`` see it: from the index, or (no index
    yet) from listing the prefix."""

    sizes: dict[str, int | None]
    records: dict[str, dict[str, Any]]
    """Key of each record file to its JSON, from the index."""
    optional: set[str]
    """Keys that may be absent: the thumbnails of the sidecars in the index."""
    index: RemoteIndex | None


def no_thumbnails_path(root: Path, remote: str) -> Path:
    return index_cache(root, remote) / "no-thumbnail.txt"


def no_thumbnails(root: Path, remote: str) -> set[str]:
    """Thumbnails a pull looked for and OSS did not have (a recording without a camera): not
    looked for again."""
    try:
        return set(no_thumbnails_path(root, remote).read_text().split())
    except OSError:
        return set()


def remote_state(root: Path, remote: str, *, config: Path | None = None) -> Remote:
    index = remote_index(root, remote, config=config)
    if index is None:
        print(f"no index on {remote} yet: listing it all")
        listed: dict[str, int | None] = dict(remote_files(remote, config=config))
        return Remote(listed, {}, set(), None)
    sizes = index.keys()
    gone = index.deleted()
    for key in list(sizes):
        episode = episode_file(key)
        if episode is not None and episode[0] in gone:
            del sizes[key]
    missing = no_thumbnails(root, remote)
    thumbnails = set()
    for key in list(sizes):
        episode = episode_file(key)
        if episode is not None and episode[1] == "json":
            thumbnails.add(episode_keys(episode[0])[2])
    thumbnails -= missing
    for thumbnail in thumbnails:
        sizes.setdefault(thumbnail, None)
    for top in LISTED_FOLDERS:
        sizes.update(remote_files(remote, f"{top}/", config=config))
    return Remote(sizes, index.record_of(), thumbnails, index)


def pull(root: Path, remote: str, *, config: Path | None = None) -> list[str]:
    """Bring down every small thing OSS has and this store lacks, then apply deletion
    records. A key on both sides with two sizes (a record: two contents) stops it before
    anything is copied."""
    state = remote_state(root, remote, config=config)
    there = state.sizes
    records = {m.group(1) for k in there if (m := RUN_RECORD.match(k))}
    ledger = Ledger(root)
    wanted: list[str] = []
    known: list[tuple[str, int]] = []
    for key, size in sorted(there.items()):
        if ignored(key) or not small(key):
            continue
        output = RUN_OUTPUT.match(key)
        if output and output.group(2) not in records:
            continue  # its run's record is not up yet: the file does not count
        path = root / "store" / key
        if path.exists():
            here = path.stat().st_size
            document = state.records.get(key)
            if size is not None and here != size:
                raise conflict(key, f"{size} bytes", f"{here} bytes", remote)
            if size is None and document is not None and not same_record(path, document):
                raise conflict(key, "one record", "another here", remote)
            if not is_pushed(ledger, key, here):
                known.append((key, here))
        else:
            wanted.append(key)
    ledger.mark(known, "pushed")
    copied = download(
        root, remote, wanted, there, config, records=state.records, optional=state.optional
    )
    missing = sorted(set(wanted) & state.optional - set(copied))
    if missing:
        with open(no_thumbnails_path(root, remote), "a") as file:
            file.write("".join(f"{k}\n" for k in missing))
    removed = apply_deletions(root)
    print(
        f"pulled {len(copied)} small files from {remote}"
        + (f"; removed {len(removed)} deleted Episodes here" if removed else "")
    )
    return copied


@dataclass
class Needs:
    """The big things a dataset or a run needs here."""

    episodes: set[str] = field(default_factory=set[str])
    files: set[str] = field(default_factory=set[str])
    """Keys of run files."""
    unknown_runs: set[str] = field(default_factory=set[str])
    """Runs whose records are not here."""


def files_under(record: RunRecord | None, path: str) -> list[str]:
    """The run files a reference to ``path`` in that run means: the file, or the files in
    that folder, as the run's record lists them (just ``path`` when its record is unknown)."""
    if record is None or path in record.files:
        return [path]
    inside = [name for name in record.files if name.startswith(path.rstrip("/") + "/")]
    return inside or [path]


def needs(root: Path, run_id: str, *, episodes: bool = True) -> Needs:
    """The big things run ``run_id`` needs, transitively by ``read``. Always its own files,
    and each run file it read (``run:<id>/<path>``), through the runs it read (whole or by
    a file) and what they read in turn. With ``episodes`` also the recordings: a whole run
    read (``run:<id>``, a dataset too) then adds its own files and its members, and an
    Episode read (or a part of it) its recording."""
    reader, library = RunReader(root), Library(root)
    found = Needs()
    seen: set[tuple[str, bool]] = set()
    stack = [(run_id, True)]  # (run, whether its own files count)
    while stack:
        current, own = stack.pop()
        if (current, own) in seen:
            continue
        seen.add((current, own))
        record = reader.get(current)
        if record is None:
            found.unknown_runs.add(current)
            continue
        if own:
            found.files.update(f"{bucketed('runs', current)}/{path}" for path in record.files)
            dataset = library.dataset(current) if episodes else None
            if dataset is not None:
                found.episodes.update(e.episode_id for e in dataset.episodes)
        for ref in record.read:
            if ref.startswith("episode:"):
                if episodes:
                    found.episodes.add(parse_episode(ref)[0])
            elif match := RUN_REFERENCE.match(ref):
                read_run, path = match.group(1), match.group(2)
                if path is not None:
                    found.files.update(
                        f"{bucketed('runs', read_run)}/{name}"
                        for name in files_under(reader.get(read_run), path)
                    )
                stack.append((read_run, path is None and episodes))
    return found


def readable(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def pull_needed(
    root: Path,
    remote: str,
    run_id: str,
    *,
    episodes: bool = True,
    link: bool = False,
    mount: Path = MOUNT,
    only: str | None = None,
) -> list[str]:
    """Bring down (or, with ``link``, link to ``mount``) the big things ``run_id`` needs
    (``needs``; without ``episodes``, no recordings); the small things first if its record
    is not here yet. With ``only``, of the run's own files just that one, or those in that
    folder (a checkpoint, without the optimizer state beside it). Nothing is written in the
    mount."""
    store = root / "store"
    if link and (root.resolve() == mount.resolve() or mount.resolve() in root.resolve().parents):
        raise SystemExit(f"{root} is in the mount {mount}, which is read only: use a local --root")
    if RunReader(root).get(run_id) is None:
        pull(root, remote)
    wanted = needs(root, run_id, episodes=episodes)
    if run_id in wanted.unknown_runs:
        raise SystemExit(f"no run {run_id} here or on OSS")
    if only is not None:
        own = bucketed("runs", run_id) + "/"
        chosen = {f"{own}{name}" for name in files_under(RunReader(root).get(run_id), only)}
        if not chosen & wanted.files:
            raise SystemExit(f"run {run_id} has no file {only}")
        wanted.files = {k for k in wanted.files if not k.startswith(own) or k in chosen}
    gone = deleted(root) & wanted.episodes
    keys: list[str] = []
    for episode_id in sorted(wanted.episodes - gone):
        keys += [k for k in episode_keys(episode_id) if not (store / k).exists()]
    keys += sorted(k for k in wanted.files if not (store / k).exists())
    done: list[str] = []
    if link:
        ledger = Ledger(root)
        rest: list[str] = []
        for key in keys:
            target = mount / "store" / key
            if small(key) or not readable(target):
                rest.append(key)
                continue
            (store / key).parent.mkdir(parents=True, exist_ok=True)
            try:
                os.symlink(target, store / key)
            except FileExistsError:
                continue
            ledger.mark([(key, target.stat().st_size)], "pushed")
            done.append(key)
        keys = rest
    if keys:
        index = remote_index(root, remote)
        shown = index.keys() if index is not None else {}
        there: dict[str, int | None] = {k: shown[k] for k in keys if shown.get(k) is not None}
        there.update(remote_sizes(remote, [k for k in keys if k not in there]))
        absent = [k for k in keys if k not in there and not thumbnail(k)]  # may not exist
        done += download(root, remote, [k for k in keys if k in there], there)
        if absent:
            print(f"not on OSS: {len(absent)} files, e.g. {absent[0]}")
    for run in sorted(wanted.unknown_runs):
        print(f"run {run} is read but its record is not here or on OSS")
    if gone:
        print(f"{len(gone)} Episodes it needs are deleted")
    print(f"{'linked or copied' if link else 'copied'} {len(done)} files for run {run_id}")
    return done


def datasets_with(root: Path, episode_id: str) -> list[tuple[str, str]]:
    """``(dataset_id, name)`` of every dataset here that lists the Episode."""
    return [
        (d.dataset_id, d.name)
        for d in Library(root).datasets()
        if any(e.episode_id == episode_id for e in d.episodes)
    ]


def delete(
    root: Path,
    remote: str,
    episode_id: str,
    *,
    reason: str,
    who: str,
    admin_config: Path,
    yes: bool = False,
    ask: Callable[[str], str] = input,
) -> bool:
    """Delete an uploaded Episode everywhere: a deletion record here, sent up with an index
    segment; its sidecar, thumbnail and recording on OSS (with the admin's ossutil config);
    then the copies here. Each other machine removes its copies at its next ``pull``.
    Whether done."""
    who = check_who(who)
    if not reason.strip():
        raise SystemExit("--reason is required")
    keys = episode_keys(episode_id)
    there = remote_sizes(remote, keys, config=admin_config)
    here = [k for k in keys if (root / "store" / k).exists()]
    if not there and not here and episode_id not in deleted(root):
        raise SystemExit(f"no Episode {episode_id} here or on OSS")
    listed = datasets_with(root, episode_id)
    print(f"Episode {episode_id}: {len(there)} files on OSS, {len(here)} here")
    print(f"datasets that list it ({len(listed)}, of those known here):")
    for dataset_id, name in listed:
        print(f"  {dataset_id}  {name}")
    if not yes and ask("delete it everywhere? this cannot be undone [yes/no] ").strip() != "yes":
        print("nothing deleted")
        return False
    if episode_id in deleted(root):  # deleted before, not finished: go on from there
        record = next(r for r in RunReader(root).runs() if f"episode:{episode_id}" in r.deletes)
    else:
        record = record_deletion(root, [episode_id], who=who, reason=reason, via="cli")
        print(f"deletion record {record.run}")
    path = record_path(root, record.run)
    own = Local(path.relative_to(root / "store").as_posix(), path, path.stat().st_size)
    ledger = Ledger(root)
    if not is_pushed(ledger, own.key, own.bytes):
        on = remote_sizes(remote, [own.key], config=admin_config).get(own.key)
        owe(root, [own])
        if on is None:
            send(root, remote, [own], ledger, admin_config)
        elif on == own.bytes:
            ledger.mark([(own.key, own.bytes)], "pushed")
        else:
            raise conflict(own.key, f"{on} bytes", f"{own.bytes} bytes", remote)
        segment = Segment(root)
        segment.add([own])
        segment.publish(root, remote, admin_config)
    # the sidecar and thumbnail first: a sidecar on OSS says its recording is there
    for key in (keys[1], keys[2], keys[0]):
        if key in there:
            ossutil("rm", reference(key, remote), "-f", config=admin_config)
    remove_episode(root, episode_id)
    print(f"deleted {episode_id} on OSS and here; other machines at their next pull")
    return True


# -- evict -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    local: Local
    episode_id: str
    sealed_unix_ns: int
    unwanted: bool
    """Its current judgment says keep=false."""


def live_reads(root: Path) -> set[str]:
    """Episodes the runs in progress here read (``local/runs/*.json``), the datasets and
    runs they read followed."""
    found: set[str] = set()
    for path in sorted((root / "local" / "runs").glob("*.json")):
        try:
            progress = json.loads(path.read_bytes())
            reads = [str(r) for r in progress.get("read", [])]
        except (OSError, ValueError, AttributeError):
            continue
        for ref in reads:
            if ref.startswith("episode:"):
                found.add(parse_episode(ref)[0])
            elif match := RUN_REFERENCE.match(ref):
                found |= needs(root, match.group(1), episodes=True).episodes
    return found


def eviction(
    root: Path, there: dict[str, int] | None, *, days: float, now_unix_ns: int
) -> list[Candidate]:
    """Recordings here that may be deleted, in order: judged keep=false first, then oldest.
    Only those pushed by the ledger and on OSS at the same size (``there``; None: not
    listed yet), sealed more than ``days`` ago, and read by no live run here."""
    ledger = Ledger(root)
    judgments = JudgmentReader(root)
    gone, live = deleted(root), live_reads(root)
    found: list[Candidate] = []
    for f in local_files(root / "store"):
        episode = episode_file(f.key)
        if episode is None or episode[1] != "mcap" or f.path.is_symlink():
            continue
        episode_id = episode[0]
        if episode_id in gone or episode_id in live or not is_pushed(ledger, f.key, f.bytes):
            continue
        if there is not None and there.get(f.key) != f.bytes:
            continue
        seal = sealed(root, episode_id)
        if seal is None or now_unix_ns - seal.sealed_unix_ns < days * 86400e9:
            continue
        current = judgments.current(episode_id)
        unwanted = current is not None and current.keep is False
        found.append(Candidate(f, episode_id, seal.sealed_unix_ns, unwanted))
    found.sort(key=lambda c: (not c.unwanted, c.sealed_unix_ns, c.episode_id))
    return found


def evict(
    root: Path,
    remote: str,
    *,
    days: float = 7.0,
    free_bytes: int | None = None,
    yes: bool = False,
    now_unix_ns: int | None = None,
) -> list[Candidate]:
    """Delete local recordings OSS holds (sidecars and thumbnails stay), in eviction order,
    until ``free_bytes`` are freed (all, if None). What was (or, without ``yes``, would be)
    deleted; a deletion here leaves a run record (store.md 2.2)."""
    now = now_unix_ns if now_unix_ns is not None else time.time_ns()
    first = eviction(root, None, days=days, now_unix_ns=now)
    if not first:
        print("nothing to free")
        return []
    there = remote_sizes(remote, (c.local.key for c in first))
    chosen: list[Candidate] = []
    total = 0
    for candidate in eviction(root, there, days=days, now_unix_ns=now):
        if free_bytes is not None and total >= free_bytes:
            break
        chosen.append(candidate)
        total += candidate.local.bytes
    unwanted = sum(c.unwanted for c in chosen)
    print(
        f"{len(chosen)} recordings older than {days:g} days are on OSS ({unwanted} judged"
        f" not kept): {total / 1e9:.1f} GB"
    )
    if not yes or not chosen:
        for c in chosen[:20]:
            print("  ", c.local.key)
        if chosen:
            print("nothing deleted; add --yes to delete them here (sidecars, thumbnails stay)")
        return chosen
    started = time.time_ns()
    judgments = JudgmentReader(root)
    judged = [
        j.ref for c in chosen if c.unwanted and (j := judgments.current(c.episode_id)) is not None
    ]
    for c in chosen:
        c.local.path.unlink(missing_ok=True)
    write_record(
        root,
        RunRecord(
            run=new_id(),
            who=program_who(),
            via="cli",
            started_unix_ns=started,
            ended_unix_ns=time.time_ns(),
            termination=Termination.FINISHED,
            read=(*(reference(c.local.key, remote) for c in chosen), *judged),
            host=socket.gethostname(),
            command=("scripts/oss.py", "evict", "--days", f"{days:g}", "--yes"),
            note="deleted here, kept on OSS: " + " ".join(c.local.key for c in chosen),
        ),
    )
    print(f"deleted {len(chosen)} recordings here, {total / 1e9:.1f} GB freed")
    return chosen


# -- status ----------------------------------------------------------------------------------


MIN_FREE_MB = 20480
"""A station's ``min_free_mb`` when its file has none (station.md): below that much free
space (MiB) it refuses to record."""
RESERVE_BYTES = MIN_FREE_MB * 2**20


def station_reserve(start: Path) -> int:
    """Bytes the default station keeps free: ``min_free_mb`` of this machine's station file
    (``XPISODE_STATION_FILE``, set by scripts/station_env.sh), else of the one the nearest
    pyproject.toml's ``[tool.xpisode] default_station`` names, else 20480 MiB."""
    named = os.environ.get("XPISODE_STATION_FILE", "").strip()
    if named:
        try:
            value = tomllib.loads(Path(named).read_text()).get("min_free_mb")
            if isinstance(value, int) and value >= 0:
                return value * 2**20
        except (OSError, ValueError):
            pass
        return RESERVE_BYTES
    for directory in (start, *start.parents):
        project = directory / "pyproject.toml"
        if not project.is_file():
            continue
        try:
            settings = tomllib.loads(project.read_text()).get("tool", {}).get("xpisode")
            if settings is None:
                continue
            station = settings.get("default_station")
            if station is None:
                break
            value = tomllib.loads((directory / station).read_text()).get("min_free_mb")
            if isinstance(value, int) and value >= 0:
                return value * 2**20
        except (OSError, ValueError):
            pass
        break
    return RESERVE_BYTES


DAY_S = 86400


@dataclass(frozen=True)
class Forecast:
    """How long the disk lasts and the backlog takes, from this host's last 24 h."""

    usable_bytes: int
    """Free bytes beyond the reserve."""
    recorded_bytes_per_s: float | None
    """Bytes per second of recording, from the sidecars of the last 24 h."""
    recording_hours_left: float | None
    written_bytes_per_s: float
    """Bytes written to the store a second over the last 24 h, recording or not."""
    wall_hours_left: float | None
    backlog_bytes: int
    drain_hours: float
    net_bytes_per_s: float | None
    """How fast the backlog grows while recording and pushing at once (negative: shrinks)."""


def forecast(
    free_bytes: int,
    recorded_bytes: int,
    recorded_s: float,
    written_bytes_24h: int,
    backlog_bytes: int,
    *,
    reserve_bytes: int = RESERVE_BYTES,
    speed_mb_s: float = SPEED_MB_S,
) -> Forecast:
    usable = max(free_bytes - reserve_bytes, 0)
    per_s = recorded_bytes / recorded_s if recorded_s > 0 else None
    written = written_bytes_24h / DAY_S
    push = speed_mb_s * 1e6
    return Forecast(
        usable_bytes=usable,
        recorded_bytes_per_s=per_s,
        recording_hours_left=usable / per_s / 3600 if per_s else None,
        written_bytes_per_s=written,
        # under a recording's worth a day says nothing about the pace
        wall_hours_left=usable / written / 3600 if written_bytes_24h >= 1e9 else None,
        backlog_bytes=backlog_bytes,
        drain_hours=backlog_bytes / push / 3600 if push > 0 else float("inf"),
        net_bytes_per_s=per_s - push if per_s is not None else None,
    )


def recorded_since(store: Path, since_unix_ns: int) -> tuple[int, float, int]:
    """Bytes and seconds of the recordings that started since then, and how many, from their
    sidecars (a sidecar stays when its recording is evicted)."""
    size, seconds, count = 0, 0.0, 0
    for path in (store / "episodes").glob("*/*/*.json"):
        try:
            sidecar = json.loads(path.read_bytes())
            episode, seal = sidecar["episode"], sidecar["seal"]
            if int(episode["start_unix_ns"]) < since_unix_ns:
                continue
            duration = (int(seal["end_ns"]) - int(episode["start_ns"])) / 1e9
            size += int(sidecar["bytes"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        seconds += max(duration, 0.0)
        count += 1
    return size, seconds, count


def hours(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f} h"


def remote_summary(root: Path, remote: str) -> str:
    """One line on what OSS holds: from its index, or listed while it has none."""
    state = remote_state(root, remote)
    if state.index is None:
        size = sum(s or 0 for s in state.sizes.values())
        return f"  OSS (no index yet, listed): {len(state.sizes)} files, {size / 1e9:.1f} GB"
    index = state.index
    sidecars = index.of("episode")
    gone = index.deleted()
    kept = [d for d in sidecars if d["episode"]["episode_id"] not in gone]
    recorded = sum(int(d.get("bytes", 0)) for d in kept)
    return (
        f"  OSS (index, {index.segments} segments): {len(kept)} Episodes,"
        f" {recorded / 1e9:.1f} GB of recordings, {len(index.of('run'))} runs,"
        f" {len(index.of('judgment'))} judgments"
    )


def status(
    root: Path,
    remote: str,
    *,
    now_unix_ns: int | None = None,
    reserve_bytes: int | None = None,
    here_only: bool = False,
) -> Forecast:
    """What this host has not pushed, what waits, and how long disk and backlog last; then
    what OSS holds, by its index (unless ``here_only``: then it reads only this host).
    ``reserve_bytes`` (default: the station's ``min_free_mb``, from the project here) is
    where recording stops."""
    reserve = reserve_bytes if reserve_bytes is not None else station_reserve(Path.cwd())
    files = local_files(root / "store")
    now_ns = now_unix_ns if now_unix_ns is not None else time.time_ns()
    left = backlog(root, now_unix_ns=now_ns, files=files)
    free = shutil.disk_usage(root).free
    now = now_ns / 1e9
    recorded, seconds, count = recorded_since(root / "store", int((now - DAY_S) * 1e9))
    written = 0
    for f in files:
        try:
            if f.path.stat().st_mtime >= now - DAY_S:
                written += f.bytes
        except OSError:
            continue
    ahead = forecast(free, recorded, seconds, written, left.unpushed_bytes, reserve_bytes=reserve)
    print(f"store: {root / 'store'}  ->  {remote}")
    print(f"  {len(files)} files here, {sum(f.bytes for f in files) / 1e9:.1f} GB")
    print(
        f"  unpushed: {left.unpushed_bytes / 1e9:.1f} GB in {len(left.ready) + len(left.waiting)}"
        f" files, about {ahead.drain_hours:.1f} h at {SPEED_MB_S:.0f} MB/s"
    )
    print(
        f"  waiting: {left.waiting_bytes / 1e9:.1f} GB, {left.waiting_episodes} Episodes"
        " (a plan's not judged yet, a free one under 10 min)"
    )
    print(
        f"  {free / 1e12:.2f} TB free on this disk,"
        f" {ahead.usable_bytes / 1e12:.2f} TB beyond the station's {reserve / 1e9:.0f} GB floor"
    )
    if ahead.recorded_bytes_per_s is None:
        print("  recording left: - (nothing recorded in the last 24 h)")
    else:
        print(
            f"  recording left: {hours(ahead.recording_hours_left)}"
            f" at {ahead.recorded_bytes_per_s / 1e6:.1f} MB per recorded second"
            f" ({count} recordings, {seconds / 3600:.1f} h, in the last 24 h)"
        )
    print(
        f"  at today's pace: {hours(ahead.wall_hours_left)}"
        f" ({written / 1e9:.1f} GB written in the last 24 h)"
    )
    if ahead.net_bytes_per_s is not None:
        verb = "grows" if ahead.net_bytes_per_s > 0 else "shrinks"
        print(
            f"  while recording and pushing, the backlog {verb} by"
            f" {abs(ahead.net_bytes_per_s) / 1e6:.1f} MB/s"
        )
    if not here_only:
        print(remote_summary(root, remote))
    return ahead


def keep_off_cameras() -> None:
    """Run this process and its ossutil on ``XPISODE_BACKGROUND_CPUS`` (e.g. ``0-3,8-11``)
    when it is set: on a station host, away from the cores that serve the cameras' USB
    interrupts, where uploads cost camera frames."""
    text = os.environ.get("XPISODE_BACKGROUND_CPUS", "").strip()
    if not text:
        return
    cpus: set[int] = set()
    for part in text.replace(" ", "").split(","):
        first, _, last = part.partition("-")
        if first:
            cpus.update(range(int(first), int(last or first) + 1))
    with contextlib.suppress(OSError, ValueError, AttributeError):
        os.sched_setaffinity(0, cpus)
    with contextlib.suppress(OSError):
        os.nice(10)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", type=Path, help="data root (default: from pyproject.toml)")
    parser.add_argument("--remote", default=REMOTE, help=f"remote prefix (default {REMOTE})")
    parser.add_argument(
        "--parallel", type=int, default=PARALLEL, help=f"parts of a file at once ({PARALLEL})"
    )
    parser.add_argument("--part-size", default=PART_SIZE, help=f"ossutil part size ({PART_SIZE})")
    parser.add_argument("--jobs", type=int, default=JOBS, help=f"files at once ({JOBS})")
    commands = parser.add_subparsers(dest="command", required=True)
    s = commands.add_parser("status")
    s.add_argument("--here", action="store_true", help="only this host; do not read OSS")
    p = commands.add_parser("push")
    only = p.add_mutually_exclusive_group()
    only.add_argument("--dataset", metavar="ID", help="this dataset first, then stop")
    only.add_argument(
        "--run", metavar="ID", help="this run and the run files it read (no recordings), then stop"
    )
    p.add_argument("--follow", action="store_true", help="keep pushing as writes arrive")
    p.add_argument("--panel", default=PANEL, help=f"panel whose writes to follow ({PANEL})")
    p.add_argument(
        "--evict-below",
        type=float,
        default=EVICT_BELOW_GB,
        metavar="GB",
        help=f"with --follow: evict when less is free (default {EVICT_BELOW_GB:g}; 0: never)",
    )
    q = commands.add_parser("pull")
    what = q.add_mutually_exclusive_group()
    what.add_argument("--dataset", metavar="ID", help="also the recordings of this dataset")
    what.add_argument("--run", metavar="ID", help="also this run's files and the run files it read")
    q.add_argument(
        "--with-episodes", action="store_true", help="with --run: the recordings it read too"
    )
    q.add_argument(
        "--only", metavar="PATH", help="with --run: of its own files just this file or folder"
    )
    q.add_argument("--link", action="store_true", help="link big files to --mount, not copy")
    q.add_argument("--mount", type=Path, default=MOUNT, help=f"read-only data root ({MOUNT})")
    d = commands.add_parser("delete")
    d.add_argument("episode_id")
    d.add_argument("--reason", required=True)
    d.add_argument("--admin-config", type=Path, required=True, help="the admin's ossutil config")
    d.add_argument("--who", default=os.environ.get("XPISODE_WHO", ""), help="default XPISODE_WHO")
    d.add_argument("--yes", action="store_true", help="do not ask")
    e = commands.add_parser("evict")
    e.add_argument("--days", type=float, default=7.0)
    e.add_argument("--free-gb", type=float, help="stop once this much is freed")
    e.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    keep_off_cameras()
    TRANSFER.parallel, TRANSFER.part_size, TRANSFER.jobs = args.parallel, args.part_size, args.jobs
    root = (args.root or data_root(Path.cwd())).resolve()
    remote = args.remote if args.remote.endswith("/") else args.remote + "/"
    if args.command == "status":
        status(root, remote, here_only=args.here)
    elif args.command == "push":
        if args.dataset is not None:
            push(root, remote, dataset=args.dataset)
        if args.run is not None:
            push(root, remote, run=args.run)
        if args.follow:
            below = int(args.evict_below * 1e9) if args.evict_below > 0 else None
            follow(root, remote, args.panel, evict_below=below)
        elif args.dataset is None and args.run is None:
            push(root, remote)
    elif args.command == "pull":
        target = args.dataset or args.run
        if target is None:
            if args.link:
                raise SystemExit("--link goes with --dataset or --run")
            pull(root, remote)
        else:
            if args.with_episodes and args.run is None:
                raise SystemExit("--with-episodes goes with --run")
            episodes = args.dataset is not None or args.with_episodes
            if args.only is not None and args.run is None:
                raise SystemExit("--only goes with --run")
            pull_needed(
                root,
                remote,
                target,
                episodes=episodes,
                link=args.link,
                mount=args.mount,
                only=args.only,
            )
    elif args.command == "delete":
        try:
            who = check_who(args.who)
        except ValueError as error:
            raise SystemExit(f"--who (or XPISODE_WHO): {error}") from None
        delete(
            root,
            remote,
            args.episode_id,
            reason=args.reason,
            who=who,
            admin_config=args.admin_config,
            yes=args.yes,
        )
    elif args.command == "evict":
        free = int(args.free_gb * 1e9) if args.free_gb is not None else None
        evict(root, remote, days=args.days, free_bytes=free, yes=args.yes)


def stop(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    # A service manager stops ``push --follow`` with SIGTERM: stop as on Ctrl+C, leaving its
    # run record and segment (a process started in the background ignores SIGINT).
    signal.signal(signal.SIGTERM, stop)
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
