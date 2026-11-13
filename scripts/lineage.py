#!/usr/bin/env python3
"""Where a checkpoint, a run or an Episode came from: the records walked backwards.

    .venv/bin/python scripts/lineage.py path/to/model.safetensors   a file, by its sha256
    .venv/bin/python scripts/lineage.py run:<id>[/<path>]
    .venv/bin/python scripts/lineage.py episode:<id>
    .venv/bin/python scripts/lineage.py --diff A B                  two of them, role by role
    .venv/bin/python scripts/lineage.py A --check                   exit 1 if a checkpoint or a
                                                                    training run does not reach
                                                                    its dataset, recipe and base
                                                                    model, or there is neither
    .venv/bin/python scripts/lineage.py A --json

First an index from every run record's ``files``, sha256 to ``run:<id>/<path>`` (and from the
sidecars, sha256 to ``episode:<id>``): a checkpoint copied out of the store is still found. Then
backwards: an Episode, the checkpoint its holding clients read, the training run that wrote it,
the export and the dataset it read, the dataset's member Episodes, their plan revision, the
plan's task version and layouts, and the station's code and configuration (the Episode's
``reads``). On the way it marks the gaps: unpinned references, things that are not there, a
sha256 no run wrote, code with uncommitted changes.

A run's reads get roles: ``dataset`` (a run that wrote ``dataset.json``, or one made from one
through runs that are not training: an export, its preparation), ``code`` (``git:``), ``base
model`` (a model hub link, or weights or anything but configuration another run wrote), ``recipe``
(a configuration file, the run's own included). A run may say them itself with
``x_lineage_roles``, reference to role, at the top of its record or in its ``params``; that comes
first.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RUN = re.compile(r"run:(\d{8}T\d{6}Z-[0-9a-f]{12})(?:/(.+))?\Z")
EPISODE = re.compile(r"episode:(\d{8}T\d{6}Z-[0-9a-f]{12})(?:#ns=(\d+),(\d+))?\Z")
PLAN = re.compile(r"plan:(\d{8}T\d{6}Z-[0-9a-f]{12})/(\d{8}T\d{6}Z-[0-9a-f]{12})\Z")
JUDGMENT = re.compile(r"judgment:(\d{8}T\d{6}Z-[0-9a-f]{12})/(\d{8}T\d{6}Z-[0-9a-f]{12})\Z")
SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
GIT = re.compile(r"git:(.+)@([0-9a-f]{40})(\+dirty)?\Z")
WEIGHTS = (".safetensors", ".pt", ".pth", ".ckpt", ".bin", ".onnx", ".msgpack", ".orbax")
CONFIGS = (".yaml", ".yml", ".toml", ".json", ".py", ".sh", ".cfg", ".ini")
HUBS = (
    "https://huggingface.co/",
    "hf:",
    "api:",
    "https://modelscope.cn/",
    "https://download.pytorch.org/",
)
ROLES = ("dataset", "task", "layout", "code", "station code", "recipe", "base model")
"""What ``--diff`` aligns; ``--check`` needs dataset, recipe and base model."""
NEEDED = ("dataset", "recipe", "base model")
MANY = 5
"""More member Episodes than this are summed up when printed."""


def data_root(start: Path) -> Path:
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


def file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(8 << 20):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class Node:
    ref: str
    role: str
    label: str = ""
    gaps: list[str] = field(default_factory=list)
    children: list[tuple[str, str]] = field(default_factory=list)
    """``(role, ref)`` of what it was made from or read."""

    def encode(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "label": self.label,
            "gaps": self.gaps,
            "children": [{"role": r, "ref": c} for r, c in self.children],
        }


class Store:
    """A data root's records, read as files are (no uniform view yet)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.store = root / "store"
        self._runs: dict[str, dict[str, Any] | None] = {}
        self._index: dict[str, str] | None = None

    def run(self, run_id: str) -> dict[str, Any] | None:
        if run_id not in self._runs:
            path = self.store / f"{bucketed('runs', run_id)}.json"
            try:
                self._runs[run_id] = json.loads(path.read_bytes())
            except (OSError, ValueError):
                self._runs[run_id] = None
        return self._runs[run_id]

    def runs(self) -> Iterable[dict[str, Any]]:
        for path in sorted((self.store / "runs").glob("*/*/*.json")):
            try:
                record = json.loads(path.read_bytes())
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and "run" in record:
                self._runs.setdefault(str(record["run"]), record)
                yield record

    @property
    def index(self) -> dict[str, str]:
        """sha256 hex to the reference of what has those bytes."""
        if self._index is None:
            found: dict[str, str] = {}
            for path in (self.store / "episodes").glob("*/*/*.json"):
                try:
                    sidecar = json.loads(path.read_bytes())
                    found.setdefault(str(sidecar["sha256"]), f"episode:{path.stem}")
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            for record in self.runs():
                for name, fact in (record.get("files") or {}).items():
                    if isinstance(fact, dict) and fact.get("sha256"):
                        found.setdefault(str(fact["sha256"]), f"run:{record['run']}/{name}")
            self._index = found
        return self._index

    def episode(self, episode_id: str) -> tuple[dict[str, Any] | None, list[str]]:
        """The Episode's header (sidecar first) and who held its groups at any time."""
        base = self.store / bucketed("episodes", episode_id)
        mcap = base.with_name(base.name + ".mcap")
        header: dict[str, Any] | None = None
        try:
            sidecar = json.loads(base.with_name(base.name + ".json").read_bytes())
            header = sidecar.get("episode")
        except (OSError, ValueError):
            header = None
        holders: list[str] = []
        if mcap.is_file():
            try:
                from xpisode_format.reader import EpisodeReader

                with EpisodeReader(mcap) as reader:
                    if header is None:
                        header = reader.header.model_dump(mode="json")
                    holders = [who for _, _, who, _ in reader.holders() if who]
            except Exception:  # noqa: BLE001 - an unreadable file still has its sidecar
                pass
        if header is not None and not holders:
            holders = [who for who in (header.get("holders") or {}).values() if who]
        return header, list(dict.fromkeys(holders))

    def plan(self, plan_id: str, revision: str) -> dict[str, Any] | None:
        path = self.store / bucketed("plans", plan_id) / f"{revision}.json"
        try:
            return json.loads(path.read_bytes())
        except (OSError, ValueError):
            return None

    def task(self, hexdigest: str) -> dict[str, Any] | None:
        path = self.store / "tasks" / f"{hexdigest}.toml"
        try:
            return tomllib.loads(path.read_text())
        except (OSError, ValueError):
            return None

    def run_file(self, run_id: str, path: str) -> Path:
        return self.store / bucketed("runs", run_id) / path


def kind_of_run(record: Mapping[str, Any]) -> str:
    """What a run was, from what it wrote and read."""
    files = list((record.get("files") or {}).keys())
    wrote = [str(w) for w in record.get("wrote") or ()]
    names = files + [w.split("/", 1)[1] for w in wrote if RUN.match(w) and "/" in w]
    if "dataset.json" in names:
        return "dataset"
    if any(n.endswith(WEIGHTS) for n in names):
        return "training"
    if any(str(w).startswith("episode:") for w in wrote):
        return "station"
    if (record.get("params") or {}).get("program"):
        return "derivation"
    return "run"


def given_roles(record: Mapping[str, Any]) -> dict[str, str]:
    """The roles a run named itself: ``x_lineage_roles``, reference to role, at the top of its
    record or in its ``params`` (where a :class:`Run` can write it)."""
    found: dict[str, str] = {}
    for place in (
        (record.get("params") or {}).get("x_lineage_roles"),
        record.get("x_lineage_roles"),
    ):
        if isinstance(place, Mapping):
            found.update({str(k): str(v) for k, v in place.items()})
    return found


def role_of(reference: str, store: Store, given: Mapping[str, str], reader: str = "") -> str:
    """The role of something the run ``reader`` read."""
    if reference in given:
        return given[reference]
    if reference.startswith("git:"):
        return "code"
    if reference.startswith(HUBS):
        return "base model"
    if reference.startswith(("episode:", "judgment:")):
        return reference.split(":", 1)[0]
    if reference.startswith("plan:"):
        return "plan"
    target = reference
    if SHA256.match(reference):
        target = store.index.get(reference.removeprefix("sha256:"), reference)
    if match := RUN.match(target):
        path = match.group(2) or ""
        if match.group(1) == reader:  # its own file (a recipe it wrote, then used): by name
            return "recipe" if path.endswith(CONFIGS) else "input"
        if path.endswith(WEIGHTS):
            return "base model"
        record = store.run(match.group(1))
        kind = kind_of_run(record) if record is not None else ""
        if kind == "dataset":
            return "dataset"
        if kind == "training":
            return "recipe" if path.endswith(CONFIGS) else "base model"
        if from_dataset(match.group(1), store, {reader}):
            return "dataset"  # an export of a dataset, a preparation of that export, ...
        if path.endswith(CONFIGS):
            return "recipe"
        return "input"
    if store.task(reference.removeprefix("sha256:")) is not None:
        return "task"
    return "input"


def from_dataset(run_id: str, store: Store, seen: set[str]) -> bool:
    """Whether the run is a dataset, or was made from one through runs that are not training
    (an export, the preparation of an export)."""
    if run_id in seen:
        return False
    seen.add(run_id)
    record = store.run(run_id)
    if record is None:
        return False
    kind = kind_of_run(record)
    if kind in ("dataset", "training", "station"):
        return kind == "dataset"
    if "dataset" in given_roles(record).values():
        return True
    for reference in record.get("read") or ():
        target = str(reference)
        if SHA256.match(target):
            target = store.index.get(target.removeprefix("sha256:"), target)
        if (match := RUN.match(target)) and from_dataset(match.group(1), store, seen):
            return True
    return False


class Graph:
    """What a thing was made from, walked backwards once per reference."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.nodes: dict[str, Node] = {}
        self.roots: list[str] = []

    # Starting points

    def start(self, what: str) -> str:
        name = ""
        if what.startswith(("run:", "episode:", "sha256:", "plan:")):
            ref = what
        else:
            path = Path(what).expanduser().resolve()
            if not path.is_file():
                raise SystemExit(f"{what}: not a reference nor a file")
            ref, name = self._file_reference(path), path.name
        if SHA256.match(ref):
            name = name or self.store.index.get(ref.removeprefix("sha256:"), "")
        weights = (name or ref).split("/")[-1].endswith(WEIGHTS)
        role = "checkpoint" if weights else self._role_alone(ref)
        self.roots.append(ref)
        self.walk(ref, role)
        return ref

    def _file_reference(self, path: Path) -> str:
        parts = path.parts
        for i, part in enumerate(parts[:-1]):
            if part == "runs" and i + 3 < len(parts) and RUN.match(f"run:{parts[i + 3]}"):
                run_id = parts[i + 3]
                if self.store.run(run_id) is not None:
                    return f"run:{run_id}/{Path(*parts[i + 4 :]).as_posix()}"
        return f"sha256:{file_sha256(path)}"

    def _role_alone(self, ref: str) -> str:
        if match := RUN.match(ref):
            record = self.store.run(match.group(1))
            if match.group(2):
                return "checkpoint" if match.group(2).endswith(WEIGHTS) else "file"
            return kind_of_run(record) if record else "run"
        return ref.split(":", 1)[0]

    # Walking

    def walk(self, ref: str, role: str) -> Node:
        if ref in self.nodes:
            return self.nodes[ref]
        node = Node(ref, role)
        self.nodes[ref] = node
        if match := RUN.match(ref):
            if match.group(2):
                self._file(node, match.group(1), match.group(2))
            else:
                self._run(node, match.group(1))
        elif match := EPISODE.match(ref):
            self._episode(node, match.group(1))
        elif match := PLAN.match(ref):
            self._plan(node, match.group(1), match.group(2))
        elif SHA256.match(ref):
            self._sha256(node)
        elif match := GIT.match(ref):
            node.label = f"{match.group(1)} @ {match.group(2)[:12]}"
            if match.group(3):
                node.gaps.append("dirty")
        elif JUDGMENT.match(ref):
            node.label = "judgment"
        else:
            node.gaps.append("unpinned")
        return node

    def _link(self, node: Node, role: str, ref: str) -> None:
        if (role, ref) not in node.children:
            node.children.append((role, ref))
        self.walk(ref, role)

    def _file(self, node: Node, run_id: str, path: str) -> None:
        record = self.store.run(run_id)
        if record is None:
            node.gaps.append("missing")
            return
        if path not in (record.get("files") or {}):
            node.gaps.append("missing")  # the run does not list it
        node.label = path
        self._link(node, kind_of_run(record), f"run:{run_id}")

    def _run(self, node: Node, run_id: str) -> None:
        record = self.store.run(run_id)
        if record is None:
            node.gaps.append("missing")
            return
        kind = kind_of_run(record)
        termination = record.get("termination", "")
        node.label = f"{kind}, {record.get('who', '?')}, {termination}"
        if kind == "dataset":
            dataset = self._dataset_file(run_id)
            if dataset is not None:
                count = len(dataset.get("episodes", ()))
                node.label = f"dataset {dataset.get('name', '')}, {count} Episodes"
        if termination not in ("finished", ""):
            node.gaps.append(str(termination))
        given = given_roles(record)
        for reference in record.get("read") or ():
            reference = str(reference)
            role = role_of(reference, self.store, given, run_id)
            if kind == "dataset" and role == "episode":
                role = "member"
            self._link(node, role, reference)

    def _dataset_file(self, run_id: str) -> dict[str, Any] | None:
        try:
            return json.loads(self.store.run_file(run_id, "dataset.json").read_bytes())
        except (OSError, ValueError):
            return None

    def _sha256(self, node: Node) -> None:
        hexdigest = node.ref.removeprefix("sha256:")
        task = self.store.task(hexdigest)
        if task is not None:
            node.role = node.role if node.role != "input" else "task"
            node.label = f"task {task.get('name', '')}"
            return
        found = self.store.index.get(hexdigest)
        if found is None:
            if node.role not in ("station config", "task"):
                node.gaps.append("no run")  # the station's files are the workspace's, not a run's
            return
        node.label = f"= {found}"
        self._link(node, "same bytes", found)

    def _episode(self, node: Node, episode_id: str) -> None:
        header, holders = self.store.episode(episode_id)
        if header is None:
            node.gaps.append("missing")
            return
        context = header.get("context") or {}
        node.label = f"{context.get('task') or '-'}, {header.get('station', {}).get('id', '')}"
        clients = header.get("clients") or {}
        for holder in holders:
            client = clients.get(holder) or {}
            reads = [str(r) for r in client.get("reads") or ()]
            checkpoint = (client.get("method") or {}).get("checkpoint")
            if checkpoint and re.match(r"[a-z][a-z0-9+.-]*:", str(checkpoint)):
                reads.insert(0, str(checkpoint))
            for reference in dict.fromkeys(reads):
                role = "code" if reference.startswith("git:") else "checkpoint"
                self._link(node, role, reference)
        collection = context.get("collection") or {}
        if collection.get("plan") and collection.get("revision"):
            self._link(node, "plan", f"plan:{collection['plan']}/{collection['revision']}")
        version = context.get("task_version")
        if version:
            self._link(node, "task", str(version))
        for reference in header.get("reads") or ():
            reference = str(reference)
            self._link(
                node,
                "station code" if reference.startswith("git:") else "station config",
                reference,
            )

    def _plan(self, node: Node, plan_id: str, revision: str) -> None:
        plan = self.store.plan(plan_id, revision)
        if plan is None:
            node.gaps.append("missing")
            return
        node.label = f"plan {plan.get('name', '')}"
        if plan.get("task"):
            self._link(node, "task", str(plan["task"]))
        for layout in (plan.get("layouts") or {}).values():
            at = layout.get("at") if isinstance(layout, dict) else None
            if at:
                self._link(node, "layout", str(at))

    # Reading the result

    def gaps(self) -> list[tuple[str, str]]:
        return [(ref, gap) for ref, node in self.nodes.items() for gap in node.gaps]

    def by_role(self) -> dict[str, set[str]]:
        """Every reference reached, by the role it was reached in."""
        found: dict[str, set[str]] = {}
        for node in self.nodes.values():
            for role, ref in node.children:
                found.setdefault(role, set()).add(ref)
        for ref in self.roots:
            found.setdefault(self.nodes[ref].role, set()).add(ref)
        return found

    def check(self) -> list[str]:
        """What keeps each checkpoint reached, and each training run started from, from its
        dataset, recipe and base model; a problem too when there was neither."""
        problems = []
        checkpoints = [r for r, n in self.nodes.items() if n.role == "checkpoint"]
        for ref in checkpoints:
            trainings = list(self._below(ref, "training"))
            if not trainings:
                problems.append(f"{ref}: no training run wrote it")
                continue
            for training in trainings:
                problems += [f"{ref}: {p}" for p in self._lacks(training)]
        trainings = [r for r in self.roots if self.nodes[r].role == "training"]
        for ref in trainings:
            problems += [f"{ref}: {p}" for p in self._lacks(ref)]
        if not checkpoints and not trainings:
            problems.append(f"{', '.join(self.roots)}: no checkpoint nor training run to check")
        return problems

    def _lacks(self, training: str) -> list[str]:
        roles = {role for role, _ in self.nodes[training].children}
        return [f"{training} read no {needed}" for needed in NEEDED if needed not in roles]

    def _below(self, ref: str, role: str) -> Iterable[str]:
        seen = set()
        stack = [ref]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            node = self.nodes[current]
            for child_role, child in node.children:
                if child_role == role:
                    yield child
                elif child_role in ("same bytes",) or (current == ref):
                    stack.append(child)

    def print(self, out: Any = None) -> None:
        out = out or sys.stdout
        shown: set[str] = set()
        for ref in self.roots:
            self._print(ref, self.nodes[ref].role, 0, shown, out)
        gaps = self.gaps()
        print(f"{len(gaps)} gap(s)" + (":" if gaps else ""), file=out)
        for ref, gap in gaps:
            print(f"  {gap}: {ref}", file=out)

    def _print(self, ref: str, role: str, depth: int, shown: set[str], out: Any) -> None:
        node = self.nodes[ref]
        pad = "  " * depth
        marks = "".join(f"  [{g}]" for g in node.gaps)
        label = f"  {node.label}" if node.label else ""
        if ref in shown:
            print(f"{pad}{role}: {ref}  (above)", file=out)
            return
        shown.add(ref)
        print(f"{pad}{role}: {ref}{label}{marks}", file=out)
        members = [c for r, c in node.children if r == "member"]
        for child_role, child in node.children:
            if child_role == "member" and len(members) > MANY:
                continue
            self._print(child, child_role, depth + 1, shown, out)
        if len(members) > MANY:
            print(f"{pad}  members: {len(members)} Episodes; what they come from:", file=out)
            below: dict[tuple[str, str], None] = {}
            for member in members:
                shown.add(member)
                for item in self.nodes[member].children:
                    below[item] = None
            missing = [m for m in members if self.nodes[m].gaps]
            if missing:
                print(f"{pad}    {len(missing)} member(s) with gaps", file=out)
            for child_role, child in below:
                self._print(child, child_role, depth + 2, shown, out)

    def encode(self) -> dict[str, Any]:
        return {
            "roots": self.roots,
            "nodes": {ref: node.encode() for ref, node in self.nodes.items()},
            "gaps": [{"ref": r, "gap": g} for r, g in self.gaps()],
            "check": self.check(),
        }


def diff(a: Graph, b: Graph) -> list[str]:
    """What differs between two graphs, role by role; code by repository."""
    lines = []
    left, right = a.by_role(), b.by_role()
    for role in ROLES:
        x, y = left.get(role, set()), right.get(role, set())
        if role in ("code", "station code"):
            lines += _diff_code(role, x, y)
            continue
        if x == y:
            lines.append(f"{role}: same" + (f" ({len(x)})" if x else " (none)"))
            continue
        lines.append(f"{role}:")
        lines += [f"  = {ref}" for ref in sorted(x & y)]  # both: e.g. one dataset, two exports
        lines += [f"  - {ref}" for ref in sorted(x - y)]
        lines += [f"  + {ref}" for ref in sorted(y - x)]
    return lines


def _diff_code(role: str, x: set[str], y: set[str]) -> list[str]:
    def by_repository(refs: set[str]) -> dict[str, set[str]]:
        found: dict[str, set[str]] = {}
        for ref in refs:
            match = GIT.match(ref)
            where = match.group(1) if match else ref
            commit = (match.group(2) + (match.group(3) or "")) if match else ref
            found.setdefault(where.rstrip("/").split("/")[-1] or where, set()).add(commit)
        return found

    left, right = by_repository(x), by_repository(y)
    lines = []
    for repository in sorted(set(left) | set(right)):
        a, b = left.get(repository, set()), right.get(repository, set())
        if a == b:
            lines.append(f"{role} {repository}: same")
        else:
            shown_a = (
                ", ".join(sorted(c[:12] + ("+dirty" if c.endswith("dirty") else "") for c in a))
                or "-"
            )
            shown_b = (
                ", ".join(sorted(c[:12] + ("+dirty" if c.endswith("dirty") else "") for c in b))
                or "-"
            )
            lines.append(f"{role} {repository}: {shown_a} -> {shown_b}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("what", nargs="?", help="a checkpoint path, run:ID[/path] or episode:ID")
    parser.add_argument("--root", type=Path, help="data root (default: from pyproject.toml)")
    parser.add_argument("--diff", nargs=2, metavar=("A", "B"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if a checkpoint or training run lacks a dataset, recipe or base model",
    )
    args = parser.parse_args(argv)
    root = (args.root or data_root(Path.cwd())).resolve()
    store = Store(root)
    if args.diff:
        graphs = []
        for what in args.diff:
            graph = Graph(store)
            graph.start(what)
            graphs.append(graph)
        lines = diff(*graphs)
        if args.json:
            print(
                json.dumps(
                    {"diff": lines, "a": graphs[0].encode(), "b": graphs[1].encode()}, indent=1
                )
            )
        else:
            print("\n".join(lines))
        problems = [p for g in graphs for p in g.check()] if args.check else []
    else:
        if not args.what:
            parser.error("give what to trace, or --diff A B")
        graph = Graph(store)
        graph.start(args.what)
        if args.json:
            print(json.dumps(graph.encode(), indent=1))
        else:
            graph.print()
        problems = graph.check() if args.check else []
    for problem in problems:
        print(f"check: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
