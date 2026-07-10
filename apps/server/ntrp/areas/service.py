from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from ntrp.areas.asks import AskStore, nominate_focus
from ntrp.areas.models import Area, Ask, AskState
from ntrp.areas.projection import page_summary
from ntrp.areas.work_models import AreaWorkSnapshot
from ntrp.constants import AREA_ATTENTION_PRESETS
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
        custodian_state: Callable[[str], dict] | None = None,
        area_work: Callable[[str], AreaWorkSnapshot] | None = None,
        work_brief: Callable[[set[str]], dict] | None = None,
    ) -> None:
        self._areas = areas
        self._asks = asks
        self._get_page = get_page
        self._pending_approvals = pending_approvals
        self._session_area = session_area
        self._area_automations = area_automations
        self._area_sessions = area_sessions
        self._get_area = get_area
        self._custodian_state = custodian_state or (lambda key: {})
        self._area_work = area_work or (lambda key: AreaWorkSnapshot())
        self._work_brief = work_brief or (lambda area_ids: {"done": [], "in_progress": []})

    def refresh_mechanical(self) -> None:
        now = datetime.now(UTC).isoformat()
        approvals: list[Ask] = []
        for row in self._pending_approvals():
            key = self._session_area(row["session_id"])
            if key is None:
                continue
            ask_id = f"approval:{row['run_id']}:{row['tool_call_id']}"
            approvals.append(Ask(
                id=ask_id, area_key=key,
                text=f"{row['tool_name']} wants: {row['preview'] or row['tool_name']}",
                kind="review", source="approval",
                actions=[{"verb": "open_session", "ref": row["session_id"]}],
                state="active", created_at=now,
                provenance=f"run:{row['run_id']}",
            ))
        self._asks.reconcile("approval", approvals)
        failures: list[Ask] = []
        for s in self._areas():
            for auto in self._area_automations(s.key):
                latest = auto.get("latest_run") or {}
                if latest.get("status") != "failed":
                    continue
                ask_id = f"runfail:{auto['task_id']}:{latest.get('id')}"
                error = latest.get("error") or "unknown failure"
                failures.append(Ask(
                    id=ask_id, area_key=s.key,
                    text=f"{auto['name']} failed — {error}",
                    kind="notify", source="run_failed",
                    actions=[{"verb": "retry", "ref": auto["name"]}],
                    state="active", created_at=now,
                    provenance=f"automation-run:{latest.get('id')}",
                ))
        self._asks.reconcile("run_failed", failures)

    def _page_summary(self, s: Area) -> dict:
        if not s.page_path:
            return {"title": s.title, "updated": "", "open_loops": [], "related": []}
        return page_summary(self._get_page(s.page_path))

    def overview(self) -> dict:
        areas = self._areas()
        active_keys = {area.key for area in areas}
        all_asks = [ask for ask in self._asks.list() if ask.area_key in active_keys]
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
        titles = {area.key: area.title for area in areas}
        work_brief = self._work_brief(active_keys)

        def enrich(rows: list[dict]) -> list[dict]:
            return [{**row, "area_title": titles.get(row["area_id"], row["area_id"])} for row in rows]

        focus_rows = [asdict(a) for a in focus]
        return {
            "areas": out,
            "focus": focus_rows,
            "brief": {
                "done": enrich(work_brief["done"]),
                "in_progress": enrich(work_brief["in_progress"]),
                "needs_you": [
                    {**ask, "area_title": titles.get(ask["area_key"], ask["area_key"])}
                    for ask in focus_rows
                ],
            },
        }

    def resolve_ask(
        self,
        ask_id: str,
        state: AskState,
        snoozed_until: str | None,
        resolution: str | None = None,
    ) -> dict:
        return asdict(self._asks.resolve(ask_id, state, snoozed_until, resolution))

    def get_ask(self, area_id: str, ask_id: str) -> Ask | None:
        return next((ask for ask in self._asks.list(area_id) if ask.id == ask_id), None)

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
        record = self._get_area(key) or {}
        autos = self._area_automations(key)
        agent_auto = next((a for a in autos if a.get("task_id") == f"area:{key}"), None)
        cust = self._custodian_state(key)
        agent = None
        if s.autonomy is not None:
            attention = record.get("attention") or "ambient"
            latest_run = (agent_auto or {}).get("latest_run") or {}
            if agent_auto is None or not agent_auto.get("enabled", True):
                availability = "unavailable"
            elif latest_run.get("status") == "failed":
                availability = "error"
            else:
                availability = "ready"
            agent = {
                # The room's liveness line — a silent-stalled custodian is the
                # documented trust killer, so this is always present.
                "last_checked": (agent_auto or {}).get("last_run_at"),
                "next_check": (agent_auto or {}).get("next_run_at"),
                "next_check_reason": cust.get("next_check_reason"),
                "last_report": cust.get("last_report"),
                "woken_by": cust.get("last_woken_by") or [],
                "runs_today": cust.get("runs_today_display", 0),
                "runs_cap": AREA_ATTENTION_PRESETS.get(attention, AREA_ATTENTION_PRESETS["ambient"])["runs_per_day"],
                "running_since": (agent_auto or {}).get("running_since"),
                "availability": availability,
                "last_error": latest_run.get("error") if availability == "error" else None,
            }
        return {
            "key": s.key, "title": s.title, "autonomy": s.autonomy,
            "page_path": s.page_path, "related": related,
            "attention": record.get("attention") or "ambient",
            "interrupts": record.get("interrupts") or "asks",
            "paused": bool(record.get("paused_at")),
            "agent": agent,
            "open_loops": summary["open_loops"], "updated": summary["updated"],
            "asks": [asdict(a) for a in self._asks.list(key)],
            "sessions": self._area_sessions(key),
            "automations": autos,
            "work": self._area_work(key).model_dump(mode="json"),
        }
