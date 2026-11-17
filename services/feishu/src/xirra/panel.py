"""Reads from the read-only xpisode panel over the central store. Nothing here writes."""

from __future__ import annotations

from typing import Any

import httpx


class Panel:
    def __init__(self, url: str, timeout: float = 60.0) -> None:
        self.url = url
        self.http = httpx.Client(base_url=url, timeout=timeout)

    def _get(self, path: str, **params: Any) -> Any:
        r = self.http.get(path, params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r.json()

    def episodes(self, **filters: Any) -> list[dict[str, Any]]:
        """Every Episode the filters select (task, station, operator, plan, kept, outcome, q)."""
        out: list[dict[str, Any]] = []
        while True:
            page = self._get("/api/v1/episodes", limit=500, offset=len(out), **filters)
            out += page["episodes"]
            if not page["episodes"] or len(out) >= page["total"]:
                return out

    def facets(self) -> dict[str, Any]:
        return self._get("/api/v1/episodes", limit=1)["facets"]

    def episode(self, episode_id: str) -> dict[str, Any]:
        return self._get(f"/api/v1/episodes/{episode_id}")

    def plans(self) -> list[dict[str, Any]]:
        return self._get("/api/v1/plans")

    def evaluation(self, plan_id: str) -> dict[str, Any]:
        return self._get(f"/api/v1/plans/{plan_id}/evaluation")

    def tasks(self) -> list[dict[str, Any]]:
        return self._get("/api/v1/tasks")

    def datasets(self) -> list[dict[str, Any]]:
        return self._get("/api/v1/datasets")

    def dataset(self, dataset_id: str) -> dict[str, Any]:
        return self._get(f"/api/v1/datasets/{dataset_id}")

    def people(self) -> list[dict[str, Any]]:
        return self._get("/api/v1/people")

    def derivations(self) -> Any:
        return self._get("/api/v1/derivations")
