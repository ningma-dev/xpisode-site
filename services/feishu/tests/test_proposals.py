from pathlib import Path
from typing import Any

import pytest

from xirra.proposals import Proposals


class FakePanel:
    def tasks(self) -> list[dict[str, Any]]:
        return [{"reference": "sha256:aa", "task": {"name": "cover_blocks", "instruction": "Cover them."}}]

    def plans(self) -> list[dict[str, Any]]:
        return [{"plan": {"plan": "p1", "name": "old_plan", "station": "yam_2", "rows": [{}] * 5}, "done": [0]}]

    def facets(self) -> dict[str, Any]:
        return {"station": {"yam_1": 3}}

    def episodes(self, **filters: Any) -> list[dict[str, Any]]:
        return [{"start_unix_ns": 0, "duration_ns": 10**10, "keep": True, "task": "cover_blocks", "outcome": None}]


class FakeWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def create_plan(self, **body: Any) -> Any:
        self.calls.append(("plan", body))
        return {"plan": {"name": body["name"], "plan": "p2"}}


def test_a_plan_is_written_only_when_its_proposer_confirms(tmp_path: Path) -> None:
    writer = FakeWriter()
    proposals = Proposals(FakePanel(), writer, tmp_path / "log.jsonl")  # type: ignore[arg-type]
    p = proposals.plan("ana", "ou_ana", "cover_blocks", "yam_2", 100)
    assert writer.calls == []
    with pytest.raises(PermissionError):
        proposals.confirm(p.id, "ou_bo")
    done = proposals.confirm(p.id, "ou_ana")
    assert done.state == "done"
    assert writer.calls[0][1]["by"] == "ana" and writer.calls[0][1]["demonstrations"] == 100
    with pytest.raises(ValueError):
        proposals.confirm(p.id, "ou_ana")  # once only


def test_a_plan_needs_a_known_task_and_station(tmp_path: Path) -> None:
    proposals = Proposals(FakePanel(), FakeWriter(), tmp_path / "log.jsonl")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="任务"):
        proposals.plan("ana", "ou", "no_such_task", "yam_2", 10)
    with pytest.raises(ValueError, match="站点"):
        proposals.plan("ana", "ou", "cover_blocks", "yam_9", 10)


def test_a_task_name_is_lower_case_words(tmp_path: Path) -> None:
    proposals = Proposals(FakePanel(), FakeWriter(), tmp_path / "log.jsonl")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        proposals.task("ana", "ou", "Insert Probe", "d", "s", "i")
