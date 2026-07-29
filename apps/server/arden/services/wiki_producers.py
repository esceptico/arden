"""Provision producer-owned wiki pages and their automations together."""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import replace

from arden.automation.models import Automation
from arden.automation.service import AutomationService
from arden.automation.triggers import MessageTrigger, Trigger, build_trigger
from arden.revisions.errors import RevisionConflictError
from arden.services.session import SessionService
from arden.services.wiki_producer_models import (
    WikiProducerPartialProvisionError,
    WikiProducerProvision,
    WikiProducerProvisionConflictError,
    WikiProducerRequest,
)
from arden.tools.tool_scope import validate_literal_tool_scope
from arden.wiki.constants import PUBLISH_WIKI_GENERATED_TOOL_NAME, READ_WIKI_PAGE_TOOL_NAME
from arden.wiki.models import GeneratedPageTarget, WikiPageRecord
from arden.wiki.pages import extract_generated_region
from arden.wiki.service import WikiService, WikiValidationError

_CONTRACT_VERSION = 1
_PRODUCER_CONTRACT_KEY = "producer_contract"
_PRODUCER_AUTOMATION_KEY = "producer_automation_id"
_REQUIRED_TOOL_SCOPE = (PUBLISH_WIKI_GENERATED_TOOL_NAME, READ_WIKI_PAGE_TOOL_NAME)


class WikiProducerProvisioner:
    def __init__(
        self,
        wiki: WikiService,
        automation: AutomationService,
        sessions: SessionService,
        registered_tool_names: Iterable[str],
    ) -> None:
        self._wiki = wiki
        self._automation = automation
        self._sessions = sessions
        self._registered_tool_names = tuple(registered_tool_names)

    async def provision(self, request: WikiProducerRequest) -> WikiProducerProvision:
        request = self._normalize(request)
        request = replace(request, model=self._automation.normalize_model(request.model))
        automation_id = self._automation_id(request.page_id)
        channel_id = self._channel_id(request.page_id)
        scope = self._tool_scope(request.source_tool_scope)
        triggers, trigger_identity = await self._prepare_trigger(request)
        fingerprint = self._fingerprint(request, trigger_identity)
        existing_automation = await self._automation.store.get(automation_id)
        snapshot = self._wiki.snapshot()
        self._require_head(snapshot.head, request.expected_head)
        record = self._record(snapshot.pages, request.page_id)

        if record is None and existing_automation is not None:
            raise WikiProducerProvisionConflictError(
                f"automation {automation_id!r} exists before its producer page {request.page_id!r}"
            )

        page_created = False
        if record is None:
            commit = self._wiki.publish_generated(
                (
                    GeneratedPageTarget(
                        page_id=request.page_id,
                        path=request.path,
                        title=request.title,
                        aliases=request.aliases,
                        generated=b"",
                        metadata={
                            _PRODUCER_AUTOMATION_KEY: automation_id,
                            _PRODUCER_CONTRACT_KEY: {"version": _CONTRACT_VERSION, "fingerprint": fingerprint},
                        },
                    ),
                ),
                source_revision=fingerprint,
                base_head=request.expected_head,
                actor="Wiki producer provisioner",
                origin="wiki.producer.provision",
                reason=f"provision wiki producer page {request.path}",
            )
            if commit is None:
                raise RuntimeError("new producer page publication produced no commit")
            record = self._wiki.read_page(request.page_id, at=commit.commit_id)
            head = commit.commit_id
            page_created = True
        else:
            self._validate_page(record, request, automation_id, fingerprint)
            head = snapshot.head
            if head is None:
                raise WikiProducerProvisionConflictError("producer page exists without a wiki revision")

        if existing_automation is not None:
            self._validate_automation(
                existing_automation,
                request,
                automation_id,
                channel_id,
                scope,
                fingerprint,
                trigger_identity,
            )
            channel_created = await self._ensure_channel(channel_id, automation_id, request.automation_name)
            existing_automation, record, head = await self._enable_and_recheck(
                existing_automation,
                request,
                automation_id,
                scope,
                fingerprint,
                trigger_identity,
                head,
            )
            return self._result(record, head, existing_automation, channel_id, False, channel_created, False)

        channel_created = await self._ensure_channel(channel_id, automation_id, request.automation_name)
        record, head = self._current_page(
            head,
            request,
            automation_id,
            fingerprint,
            require_initial_baseline=True,
        )
        automation_created = False
        try:
            automation = await self._automation.create(
                name=request.automation_name,
                description=request.automation_name,
                prompt=request.prompt,
                model=request.model,
                triggers=triggers,
                triggers_resolved=True,
                auto_approve=True,
                tool_scope=list(scope),
                thread_id=channel_id,
                read_history=True,
                task_id=automation_id,
                idempotency_key=f"wiki-producer:{fingerprint}",
                idempotency_scope="global",
                enabled=False,
            )
            automation_created = automation is not None
        except WikiProducerProvisionConflictError:
            raise
        except Exception as exc:
            automation = await self._automation.store.get(automation_id)
            if automation is None:
                raise WikiProducerPartialProvisionError(request.page_id, automation_id) from exc
        if automation is None:
            automation = await self._automation.store.get(automation_id)
            if automation is None:
                raise WikiProducerProvisionConflictError(
                    f"automation idempotency claim already exists for {automation_id!r}"
                )
        self._validate_automation(
            automation,
            request,
            automation_id,
            channel_id,
            scope,
            fingerprint,
            trigger_identity,
        )
        automation, record, head = await self._enable_and_recheck(
            automation,
            request,
            automation_id,
            scope,
            fingerprint,
            trigger_identity,
            head,
        )
        return self._result(
            record,
            head,
            automation,
            channel_id,
            page_created,
            channel_created,
            automation_created,
        )

    async def _enable_and_recheck(
        self,
        automation: Automation,
        request: WikiProducerRequest,
        automation_id: str,
        scope: tuple[str, ...],
        fingerprint: str,
        trigger_identity: Mapping[str, object],
        head: str,
    ) -> tuple[Automation, WikiPageRecord, str]:
        if automation.enabled:
            await self._require_channel(self._channel_id(request.page_id), automation_id)
            refreshed = await self._automation.store.get(automation_id)
            if refreshed is None or not refreshed.enabled:
                raise WikiProducerProvisionConflictError(f"automation {automation_id!r} changed before return")
            self._validate_automation(
                refreshed,
                request,
                automation_id,
                self._channel_id(request.page_id),
                scope,
                fingerprint,
                trigger_identity,
            )
            record, current_head = self._current_page(
                head,
                request,
                automation_id,
                fingerprint,
                require_initial_baseline=False,
            )
            return refreshed, record, current_head

        await self._require_channel(self._channel_id(request.page_id), automation_id)
        self._current_page(
            head,
            request,
            automation_id,
            fingerprint,
            require_initial_baseline=True,
        )
        claim_key = f"wiki-producer:{fingerprint}"
        enabled_here = False
        try:
            enabled_here = await self._automation.store.set_enabled_if_claim(
                automation_id,
                claim_key,
                expected=False,
                enabled=True,
            )
            refreshed = await self._automation.store.get(automation_id)
            if refreshed is None or not refreshed.enabled:
                raise WikiProducerProvisionConflictError(f"automation {automation_id!r} disappeared before enable")
            self._validate_automation(
                refreshed,
                request,
                automation_id,
                self._channel_id(request.page_id),
                scope,
                fingerprint,
                trigger_identity,
            )
            await self._require_channel(self._channel_id(request.page_id), automation_id)
            record, current_head = self._current_page(
                head,
                request,
                automation_id,
                fingerprint,
                require_initial_baseline=True,
            )
        except BaseException:
            if enabled_here:
                await self._automation.store.set_enabled_if_claim(
                    automation_id,
                    claim_key,
                    expected=True,
                    enabled=False,
                )
            raise
        return refreshed, record, current_head

    @staticmethod
    def _result(
        page: WikiPageRecord,
        head: str,
        automation: Automation,
        channel_id: str,
        page_created: bool,
        channel_created: bool,
        automation_created: bool,
    ) -> WikiProducerProvision:
        return WikiProducerProvision(
            page_id=page.page.page_id,
            path=page.resource.path,
            title=page.page.title,
            aliases=page.page.aliases,
            page_version=page.resource.version_id,
            head=head,
            automation_id=automation.task_id,
            automation_name=automation.name,
            model=automation.model,
            auto_approve=automation.auto_approve,
            tool_scope=tuple(automation.tool_scope or ()),
            channel_id=channel_id,
            page_created=page_created,
            channel_created=channel_created,
            automation_created=automation_created,
        )

    async def _ensure_channel(self, channel_id: str, automation_id: str, name: str) -> bool:
        _state, created = await self._sessions.provision_if_absent(
            name=name,
            session_type="channel",
            session_id=channel_id,
            origin_automation_id=automation_id,
        )
        await self._require_channel(channel_id, automation_id)
        return created

    async def _require_channel(self, channel_id: str, automation_id: str) -> None:
        existing = await self._sessions.store.load_session(channel_id)
        if existing is None:
            raise WikiProducerProvisionConflictError(f"channel {channel_id!r} disappeared during provisioning")
        state = existing.state
        if (
            state.session_type != "channel"
            or state.origin_automation_id != automation_id
            or await self._sessions.store.is_session_archived(channel_id)
        ):
            raise WikiProducerProvisionConflictError(f"channel {channel_id!r} belongs to another owner")

    def _normalize(self, request: WikiProducerRequest) -> WikiProducerRequest:
        page_id = request.page_id.strip()
        path = request.path.strip()
        title = request.title.strip()
        automation_name = request.automation_name.strip()
        prompt = request.prompt.strip()
        aliases = tuple(alias.strip() for alias in request.aliases)
        source_tool_scope = tuple(sorted(set(scope.strip() for scope in request.source_tool_scope)))
        channels = tuple(channel.strip() for channel in request.channels) if request.channels is not None else None
        contains = tuple(value.strip() for value in request.contains) if request.contains is not None else None
        model = request.model.strip() if request.model is not None else None
        from_user = request.from_user.strip() if request.from_user is not None else None
        if not page_id or not title or not automation_name or not prompt:
            raise ValueError("page_id, title, automation_name, and prompt are required")
        if any(byte in page_id for byte in ("\x00", "\x1f")):
            raise ValueError("page_id must not contain NUL or unit-separator bytes")
        if len(automation_name) > 220:
            raise ValueError("automation_name must be at most 220 characters")
        if not path.startswith(("feeds/", "insights/")) or not path.endswith(".md"):
            raise WikiValidationError("producer pages must be under feeds/ or insights/ and end in .md")
        if not all(aliases) or len(set(aliases)) != len(aliases):
            raise ValueError("aliases must be nonempty and unique")
        if not all(source_tool_scope):
            raise ValueError("source_tool_scope entries must be nonempty")
        if channels is not None and not all(channels):
            raise ValueError("channels must be nonempty")
        if contains is not None and not all(contains):
            raise ValueError("contains entries must be nonempty")
        if from_user == "":
            raise ValueError("from_user must be nonempty")
        if request.trigger_type == "message" and not channels:
            raise ValueError("message producer triggers require at least one channel")
        return WikiProducerRequest(
            page_id=page_id,
            path=path,
            title=title,
            aliases=aliases,
            automation_name=automation_name,
            prompt=prompt,
            model=model or None,
            trigger_type=request.trigger_type,
            at=request.at,
            days=request.days,
            every=request.every,
            start=request.start,
            end=request.end,
            event_type=request.event_type,
            lead_minutes=request.lead_minutes,
            channels=channels,
            from_user=from_user,
            contains=contains,
            source_tool_scope=source_tool_scope,
            expected_head=request.expected_head,
        )

    def _tool_scope(self, source_scope: tuple[str, ...]) -> tuple[str, ...]:
        scope = tuple(sorted({*source_scope, *_REQUIRED_TOOL_SCOPE}))
        validate_literal_tool_scope(scope, self._registered_tool_names)
        return scope

    @staticmethod
    def _record(records: tuple[WikiPageRecord, ...], page_id: str) -> WikiPageRecord | None:
        return next((record for record in records if record.page.page_id == page_id), None)

    @staticmethod
    def _require_head(actual: str | None, expected: str | None) -> None:
        if actual != expected:
            raise RevisionConflictError(f"wiki changed: expected head {expected!r}, found {actual!r}")

    @staticmethod
    def _automation_id(page_id: str) -> str:
        return f"wiki-producer:{page_id}"

    @staticmethod
    def _channel_id(page_id: str) -> str:
        return f"wiki-producer:{page_id}:channel"

    def _current_page(
        self,
        expected_head: str,
        request: WikiProducerRequest,
        automation_id: str,
        fingerprint: str,
        *,
        require_initial_baseline: bool,
    ) -> tuple[WikiPageRecord, str]:
        snapshot = self._wiki.snapshot()
        self._require_head(snapshot.head, expected_head)
        record = self._record(snapshot.pages, request.page_id)
        if record is None:
            raise WikiProducerProvisionConflictError(f"wiki page {request.page_id!r} disappeared")
        self._validate_page(record, request, automation_id, fingerprint)
        if require_initial_baseline:
            self._validate_initial_baseline(record, fingerprint)
        if snapshot.head is None:
            raise WikiProducerProvisionConflictError("producer page exists without a wiki revision")
        return record, snapshot.head

    async def _prepare_trigger(
        self,
        request: WikiProducerRequest,
    ) -> tuple[list[dict], Mapping[str, object]]:
        if request.trigger_type != "message":
            trigger, _next_run = build_trigger(
                request.trigger_type,
                at=request.at,
                days=request.days,
                every=request.every,
                event_type=request.event_type,
                lead_minutes=request.lead_minutes,
                start=request.start,
                end=request.end,
            )
            payload = {"type": trigger.type, **trigger.params()}
            return [payload], payload

        raw: dict[str, object] = {
            "type": "message",
            "source": "slack",
            "channels": list(request.channels or ()),
        }
        if request.from_user is not None:
            raw["from_user"] = request.from_user
        if request.contains is not None:
            raw["contains"] = list(request.contains)
        resolved = await self._automation.resolve_message_trigger(raw)
        channels_by_id = {
            str(channel["id"]): {"id": str(channel["id"]), "name": str(channel["name"])}
            for channel in resolved.channels
        }
        contains = sorted({value.casefold() for value in resolved.contains})
        canonical = MessageTrigger(
            source=resolved.source,
            channels=[channels_by_id[channel_id] for channel_id in sorted(channels_by_id)],
            from_user_id=resolved.from_user_id,
            from_user_name=resolved.from_user_name,
            contains=contains,
        )
        return [{"type": "message", **canonical.params()}], self._trigger_identity(canonical)

    @staticmethod
    def _trigger_identity(trigger: Trigger) -> Mapping[str, object]:
        if isinstance(trigger, MessageTrigger):
            return {
                "type": "message",
                "source": trigger.source,
                "channel_ids": sorted({str(channel["id"]) for channel in trigger.channels}),
                "from_user_id": trigger.from_user_id,
                "contains": sorted({value.casefold() for value in trigger.contains}),
            }
        return {"type": trigger.type, **trigger.params()}

    @staticmethod
    def _fingerprint(request: WikiProducerRequest, trigger_identity: Mapping[str, object]) -> str:
        contract = {
            "version": _CONTRACT_VERSION,
            "page_id": request.page_id,
            "path": request.path,
            "title": request.title,
            "aliases": request.aliases,
            "automation_name": request.automation_name,
            "prompt": request.prompt,
            "model": request.model,
            "trigger": trigger_identity,
            "source_tool_scope": request.source_tool_scope,
        }
        encoded = json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(b"wiki.producer.contract.v1\0" + encoded).hexdigest()

    @staticmethod
    def _validate_page(
        record: WikiPageRecord,
        request: WikiProducerRequest,
        automation_id: str,
        fingerprint: str,
    ) -> None:
        contract = record.page.metadata.get(_PRODUCER_CONTRACT_KEY)
        if (
            record.page.lifecycle != "active"
            or record.resource.path != request.path
            or record.page.title != request.title
            or record.page.aliases != request.aliases
            or record.page.metadata.get(_PRODUCER_AUTOMATION_KEY) != automation_id
            or "fact_citations" in record.page.metadata
            or not isinstance(contract, Mapping)
            or contract != {"version": _CONTRACT_VERSION, "fingerprint": fingerprint}
        ):
            raise WikiProducerProvisionConflictError(f"wiki page {request.page_id!r} has a different producer contract")

    @staticmethod
    def _validate_initial_baseline(record: WikiPageRecord, fingerprint: str) -> None:
        if (
            record.page.metadata.get("generated_from_revision") != fingerprint
            or extract_generated_region(record.content, expected_page_id=record.page.page_id) != b""
        ):
            raise WikiProducerProvisionConflictError(
                f"wiki page {record.page.page_id!r} changed before its producer was enabled"
            )

    @staticmethod
    def _validate_automation(
        automation: Automation,
        request: WikiProducerRequest,
        automation_id: str,
        channel_id: str,
        scope: tuple[str, ...],
        fingerprint: str,
        trigger_identity: Mapping[str, object],
    ) -> None:
        trigger_matches = (
            len(automation.triggers) == 1
            and WikiProducerProvisioner._trigger_identity(automation.triggers[0]) == trigger_identity
        )
        if (
            automation.task_id != automation_id
            or automation.name != request.automation_name
            or automation.description != request.automation_name
            or automation.description_source != "manual"
            or automation.prompt != request.prompt
            or automation.model != request.model
            or automation.auto_approve is not True
            or automation.thread_id != channel_id
            or automation.read_history is not True
            or automation.tool_scope != list(scope)
            or automation.idempotency_key != f"wiki-producer:{fingerprint}"
            or automation.idempotency_scope != "global"
            or not trigger_matches
        ):
            raise WikiProducerProvisionConflictError(f"automation {automation_id!r} has a different producer contract")
