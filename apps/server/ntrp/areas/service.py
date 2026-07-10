from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from ntrp.areas.asks import AskStore, nominate_focus
from ntrp.areas.models import Area, Ask, AskState
from ntrp.areas.projection import page_summary
from ntrp.memory.pages import Page


class AreaService:
    def __init__(
        self,
        areas: Callable[[], list[Area]],
        asks: AskStore,
        get_page: Callable[[str], Page],
        pending_approvals: Callable[[], list[dict]],
        session_area: Callable[[str], str | None],
        area_automations: Callable[[str], list[dict]],
        area_sessions: Callable[[str], list[dict]],
        get_area: Callable[[str], dict | None],
    ) -> None:
        self._areas = areas
        self._asks = asks
        self._get_page = get_page
        self._pending_approvals = pending_approvals
        self._session_area = session_area
        self._area_automations = area_automations
        self._area_sessions = area_sessions
        self._get_area = get_area

    def refresh_mechanical(self) -> None:
        now = datetime.now(UTC).isoformat()
        existing = {a.id for a in self._asks.list(include_resolved=True)}
        for row in self._pending_approvals():
            key = self._session_area(row["session_id"])
            if key is None:
                continue
            ask_id = f"approval:{row['run_id']}:{row['tool_call_id']}"
            if ask_id in existing:
                continue
            self._asks.upsert(Ask(
                id=ask_id, area_key=key,
                text=f"{row['tool_name']} wants: {row['preview'] or row['tool_name']}",
                kind="decide", source="approval",
                actions=[{"verb": "open_session", "ref": row["session_id"]}],
                state="active", created_at=now,
                provenance=f"run:{row['run_id']}",
            ))
        for s in self._areas():
            for auto in self._area_automations(s.key):
                if not auto.get("last_result") or not str(auto["last_result"]).startswith("error"):
                    continue
                ask_id = f"runfail:{auto['name']}:{auto['last_run_at']}"
                if ask_id in existing:
                    continue
                self._asks.upsert(Ask(
                    id=ask_id, area_key=s.key,
                    text=f"{auto['name']} failed — {auto['last_result']}",
                    kind="review", source="run_failed",
                    actions=[{"verb": "retry", "ref": auto["name"]}],
                    state="active", created_at=now,
                ))

    def _page_summary(self, s: Area) -> dict:
        if not s.page_path:
            return {"title": s.title, "updated": "", "open_loops": [], "related": []}
        return page_summary(self._get_page(s.page_path))

    def overview(self) -> dict:
        areas = self._areas()
        all_asks = self._asks.list()
        focus = nominate_focus(all_asks)
        out = []
        for s in areas:
            summary = self._page_summary(s)
            area_asks = [a for a in all_asks if a.area_key == s.key]
            out.append({
                "key": s.key, "title": s.title, "autonomy": s.autonomy,
                "page_path": s.page_path,
                "live": bool(area_asks) or bool(
                    any(a.get("running_since") for a in self._area_automations(s.key))
                ),
                "updated": summary["updated"], "ask_count": len(area_asks),
            })
        return {"areas": out, "focus": [asdict(a) for a in focus]}

    def resolve_ask(self, ask_id: str, state: AskState, snoozed_until: str | None) -> dict:
        return asdict(self._asks.resolve(ask_id, state, snoozed_until))

    def detail(self, key: str) -> dict:
        areas = self._areas()
        by_key = {s.key: s for s in areas}
        s = by_key.get(key)
        if s is None:
            # Universal rooms: a plain container (no capabilities) still opens
            # as a bare room — its sessions and automations, no page sections.
            area = self._get_area(key)
            if area is None:
                raise KeyError(f"unknown container '{key}'; valid: {list(by_key)}")
            s = Area(key=key, title=area["name"], page_path=None, autonomy=None)
        summary = self._page_summary(s)
        # Related = the page's `## Related` wikilinks — page SLUGS, mapped to
        # the areas whose page stem matches (identity is area_id now).
        slug_to_key = {Path(sl.page_path).stem: sl.key for sl in areas if sl.page_path}
        related = [
            slug_to_key[slug]
            for slug in dict.fromkeys(summary["related"])
            if slug in slug_to_key and slug_to_key[slug] != key
        ]
        return {
            "key": s.key, "title": s.title, "autonomy": s.autonomy,
            "page_path": s.page_path, "related": related,
            "open_loops": summary["open_loops"], "updated": summary["updated"],
            "asks": [asdict(a) for a in self._asks.list(key)],
            "sessions": self._area_sessions(key),
            "automations": self._area_automations(key),
        }
