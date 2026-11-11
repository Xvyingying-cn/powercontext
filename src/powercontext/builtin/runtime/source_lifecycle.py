# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Source-backed lifecycle propagation for Memory and Topic Memory availability.

Callers register an evidence dependency graph per Scope: Source nodes carry the
availability driven by lifecycle events, Memory nodes reference an exact
MemoryCitation, and Topic nodes reference a Topic Memory ArtifactRef. Derived
nodes evaluate ``all``/``any`` support policies over their registered parents;
withdrawal and restoration propagate through the whole DAG.

The lifecycle ledger below is the availability source of truth. Native Memory
entry states are a projection kept in step by an idempotent reconcile pass, so
an interrupted propagation is completed by replaying the same event, and a
successful replay never creates another Memory revision.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator
from sqlalchemy import insert, select, update

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.persistence.tables import (
    SOURCE_LIFECYCLE_EVENTS_TABLE,
    SOURCE_LIFECYCLE_NODES_TABLE,
)
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef
from powercontext.builtin.statistics import ModelUsagePurpose

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Awaitable, Callable
    from typing import Protocol

    from sqlalchemy import AsyncConnection

    class _LifecycleDatabase(Protocol):
        def transaction(self) -> Any: ...

    from powercontext.builtin.runtime.application import BuiltinRuntime


_LIFECYCLE_NODE_KINDS = ("source", "memory", "topic")
_LIFECYCLE_POLICIES = ("all", "any")
_LIFECYCLE_PURPOSES = ("generation", "audit")


class SourceLifecycleError(ValueError):
    """Raised for registration conflicts, invalid events, and denied resolution."""


class SourceLifecycleEvent(BaseModel):
    """One availability change for a registered Source node.

    ``revision`` must match the Source's registered lifecycle version exactly
    and ``sequence`` must be strictly increasing per Source; identical replays
    are idempotent while any other lower or colliding sequence is rejected.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: StrictStr
    reason: StrictStr
    revision: StrictInt = Field(ge=1)
    sequence: StrictInt = Field(ge=1)
    available: StrictBool

    @field_validator("node_id", "reason")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be nonempty and not only whitespace")  # noqa: TRY003
        return value


@dataclass(frozen=True)
class _LifecycleNode:
    """One persisted dependency-graph node with its projected availability."""

    node_id: str
    kind: Literal["source", "memory", "topic"]
    policy: Literal["all", "any"]
    parents: tuple[str, ...]
    revision: int | None
    active: bool
    binding_json: str
    artifact_id: str | None

    def binding(self) -> SourceRef | MemoryCitation | ArtifactRef:
        payload = json.loads(self.binding_json)
        if self.kind == "source":
            return SourceRef.model_validate(payload)
        if self.kind == "memory":
            return MemoryCitation.model_validate(payload)
        return ArtifactRef.model_validate(payload)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _event_digest(event: SourceLifecycleEvent) -> str:
    payload = _canonical_json(
        {
            "available": event.available,
            "node_id": event.node_id,
            "reason": event.reason,
            "revision": event.revision,
            "sequence": event.sequence,
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SourceLifecycleApplication:
    """Runtime entry point exposing per-scope lifecycle services."""

    def __init__(self, runtime: BuiltinRuntime, database: Any) -> None:
        self._runtime = runtime
        self._database = database

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycleService:
        return ScopedSourceLifecycleService(self._runtime, self._database, scope_id)

    async def hidden_topic_artifact_ids(self, scope_id: str, /) -> frozenset[str]:
        """Topic artifact ids currently without support in this Scope."""

        table = SOURCE_LIFECYCLE_NODES_TABLE
        statement = (
            select(table.c.artifact_id)
            .where(
                table.c.scope_id == scope_id,
                table.c.kind == "topic",
                table.c.active == False,  # noqa: E712 - SQL boolean comparison
            )
            .where(table.c.artifact_id.is_not(None))
        )
        async with self._database.transaction() as connection:
            rows = (await connection.execute(statement)).all()
        return frozenset(str(row[0]) for row in rows)


class ScopedSourceLifecycleService:
    """Register evidence dependencies and propagate availability for one Scope."""

    def __init__(self, runtime: BuiltinRuntime, database: Any, scope_id: str) -> None:
        self._runtime = runtime
        self._database = database
        self.scope_id = validate_scope_id(scope_id)

    async def register_source(
        self,
        node_id: str,
        source_ref: SourceRef,
        /,
        *,
        revision: int,
    ) -> None:
        if type(revision) is not int or revision < 1:
            raise SourceLifecycleError("source-revision")
        await self._register(
            node_id,
            kind="source",
            binding=source_ref,
            parents=(),
            policy="all",
            revision=revision,
        )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        /,
        *,
        parents: tuple[str, ...],
        policy: Literal["all", "any"],
    ) -> None:
        await self._register(node_id, kind="memory", binding=citation, parents=parents, policy=policy, revision=None)

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        /,
        *,
        parents: tuple[str, ...],
        policy: Literal["all", "any"],
    ) -> None:
        await self._register(node_id, kind="topic", binding=artifact_ref, parents=parents, policy=policy, revision=None)

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        """Apply one Source availability event and return sorted active node ids.

        The event ledger commit and the availability recompute share one
        transaction; the native Memory projection afterwards is idempotent, so
        replaying the same event completes any interrupted propagation without
        creating additional Memory revisions.
        """

        digest = _event_digest(event)
        changed: list[str] = []
        async with self._database.transaction() as connection:
            nodes = await self._load_nodes(connection)
            node = nodes.get(event.node_id)
            if node is None or node.kind != "source":
                raise SourceLifecycleError("unknown-source")
            if node.revision != event.revision:
                raise SourceLifecycleError("event-revision")
            prior = await self._load_event(connection, event.node_id, event.sequence)
            replay = prior is not None
            if replay:
                stored = prior  # type: ignore[unreachable]
                if (
                    stored["digest"] != digest
                    or stored["revision"] != event.revision
                    or stored["available"] != event.available
                    or stored["reason"] != event.reason
                ):
                    raise SourceLifecycleError("event-sequence")
            else:
                last = await self._last_sequence(connection, event.node_id)
                if last is not None and event.sequence <= last:
                    raise SourceLifecycleError("event-sequence")
                await connection.execute(
                    insert(SOURCE_LIFECYCLE_EVENTS_TABLE).values(
                        scope_id=self.scope_id,
                        node_id=event.node_id,
                        sequence=event.sequence,
                        revision=event.revision,
                        available=event.available,
                        reason=event.reason,
                        digest=digest,
                    )
                )
            availability = {key: value.active for key, value in nodes.items() if value.kind == "source"}
            if not replay:
                availability[event.node_id] = event.available
            resolved: dict[str, bool] = {}

            def active_of(key: str) -> bool:
                if key in resolved:
                    return resolved[key]
                current = nodes[key]
                if current.kind == "source":
                    resolved[key] = availability[key]
                    return resolved[key]
                support = (active_of(parent) for parent in current.parents)
                resolved[key] = all(support) if current.policy == "all" else any(support)
                return resolved[key]

            for node_id, current in nodes.items():
                target = active_of(node_id)
                if target != current.active:
                    await connection.execute(
                        update(SOURCE_LIFECYCLE_NODES_TABLE)
                        .where(
                            SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                            SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
                        )
                        .values(active=target)
                    )
                    if current.kind == "memory":
                        changed.append(node_id)
            refreshed = dict(nodes)
            for node_id, target in resolved.items():
                refreshed[node_id] = _replace_active(refreshed[node_id], target)
        if replay:
            # A replay after an interrupted propagation reconciles every
            # registered Memory node; matching states are left untouched.
            projection_targets = [node_id for node_id, value in refreshed.items() if value.kind == "memory"]
        else:
            projection_targets = changed
        for node_id in projection_targets:
            await self._project_memory(refreshed[node_id], event.reason)
        return sorted(node_id for node_id, node in refreshed.items() if node.active)

    async def resolve(
        self,
        node_id: str,
        /,
        *,
        purpose: Literal["generation", "audit"],
    ) -> SourceRef | MemoryCitation | ArtifactRef:
        if purpose not in _LIFECYCLE_PURPOSES:
            raise SourceLifecycleError("purpose")
        async with self._database.transaction() as connection:
            nodes = await self._load_nodes(connection)
        node = nodes.get(node_id)
        if node is None:
            raise SourceLifecycleError("unknown-node")
        if purpose == "generation" and not node.active:
            raise SourceLifecycleError("node-unavailable")
        return node.binding()

    async def _register(
        self,
        node_id: str,
        /,
        *,
        kind: Literal["source", "memory", "topic"],
        binding: SourceRef | MemoryCitation | ArtifactRef,
        parents: tuple[str, ...],
        policy: Literal["all", "any"],
        revision: int | None,
    ) -> None:
        if not isinstance(node_id, str) or not node_id.strip():
            raise SourceLifecycleError("node-id")
        if policy not in _LIFECYCLE_POLICIES:
            raise SourceLifecycleError("policy")
        if kind not in _LIFECYCLE_NODE_KINDS:
            raise SourceLifecycleError("kind")
        if kind == "source":
            if parents:
                raise SourceLifecycleError("parents")
        elif not parents:
            raise SourceLifecycleError("parents")
        if len(set(parents)) != len(parents) or node_id in parents:
            raise SourceLifecycleError("parents")
        binding_json = _canonical_json(binding.model_dump(mode="json"))
        artifact_id = binding.artifact_id if isinstance(binding, ArtifactRef) else None
        if kind == "memory":
            await self._require_memory_readable(binding)  # type: ignore[arg-type]
        elif kind == "topic":
            await self._require_topic_readable(binding)  # type: ignore[arg-type]
        else:
            await self._require_source_readable(binding)  # type: ignore[arg-type]
        async with self._database.transaction() as connection:
            nodes = await self._load_nodes(connection)
            for parent in parents:
                if parent not in nodes:
                    raise SourceLifecycleError("parent-not-registered")
            existing = nodes.get(node_id)
            if existing is not None:
                if (
                    existing.kind == kind
                    and existing.binding_json == binding_json
                    and existing.parents == tuple(parents)
                    and existing.policy == policy
                    and existing.revision == revision
                ):
                    return
                raise SourceLifecycleError("node-identity-conflict")
            if kind == "source":
                active = True
            else:
                support = [nodes[parent].active for parent in parents]
                active = all(support) if policy == "all" else any(support)
            await connection.execute(
                insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                    scope_id=self.scope_id,
                    node_id=node_id,
                    kind=kind,
                    policy=policy,
                    revision=revision,
                    active=active,
                    binding=binding_json.encode("utf-8"),
                    parents=_canonical_json(list(parents)).encode("utf-8"),
                    artifact_id=artifact_id,
                )
            )
        if kind == "memory" and not active:
            await self._project_memory(
                _LifecycleNode(
                    node_id=node_id,
                    kind=kind,
                    policy=policy,
                    parents=tuple(parents),
                    revision=revision,
                    active=active,
                    binding_json=binding_json,
                    artifact_id=artifact_id,
                ),
                "source lifecycle registration",
            )

    async def _project_memory(self, node: _LifecycleNode, reason: str) -> None:
        """Reconcile one Memory node's native entry state with its ledger state.

        Direct service access keeps the operation revision-independent: state
        changes never invalidate the registered citation, and matching states
        are left untouched so replays add no revisions.
        """

        citation = node.binding()
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("memory-binding")
        async with self._runtime._context(
            self.scope_id,
            embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING,
        ) as context:
            async with self._runtime._locked(self.scope_id):
                service = context.artifacts.memory
                current = await service.head(context.artifacts.memory_artifact_id)
                if current is None:
                    return
                manifest = {item.entry_id: item for item in current.content.manifest.entries}
                listed = manifest.get(citation.entry_id)
                if listed is None:
                    return
                target_active = node.active
                if (listed.state == "active") == target_active:
                    return
                version = next(
                    item for item in await service.entries(current) if item.entry_id == citation.entry_id
                )
                if target_active:
                    await service.reactivate(current, entries=(version,), reason=reason)
                else:
                    await service.forget(current, entries=(version,), reason=reason)

    async def _require_source_readable(self, source_ref: SourceRef) -> None:
        try:
            await self._runtime.records.for_scope(self.scope_id).get_source(
                source_ref.source_type, source_ref.source_id
            )
        except Exception as error:
            raise SourceLifecycleError("unreadable-source") from error

    async def _require_memory_readable(self, citation: MemoryCitation) -> None:
        try:
            await self._runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=citation))
        except Exception as error:
            raise SourceLifecycleError("unreadable-memory") from error

    async def _require_topic_readable(self, artifact_ref: ArtifactRef) -> None:
        try:
            await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                artifact_ref.family, artifact_ref.artifact_id, artifact_ref.revision
            )
        except Exception as error:
            raise SourceLifecycleError("unreadable-topic") from error

    async def _load_nodes(self, connection: AsyncConnection) -> dict[str, _LifecycleNode]:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        rows = (
            await connection.execute(select(table).where(table.c.scope_id == self.scope_id))
        ).mappings().all()
        nodes: dict[str, _LifecycleNode] = {}
        for row in rows:
            nodes[row["node_id"]] = _LifecycleNode(
                node_id=str(row["node_id"]),
                kind=str(row["kind"]),
                policy=str(row["policy"]),
                parents=tuple(json.loads(bytes(row["parents"]).decode("utf-8"))),
                revision=None if row["revision"] is None else int(row["revision"]),
                active=bool(row["active"]),
                binding_json=bytes(row["binding"]).decode("utf-8"),
                artifact_id=None if row["artifact_id"] is None else str(row["artifact_id"]),
            )
        return nodes

    async def _load_event(
        self,
        connection: AsyncConnection,
        node_id: str,
        sequence: int,
    ) -> dict[str, Any] | None:
        table = SOURCE_LIFECYCLE_EVENTS_TABLE
        row = (
            await connection.execute(
                select(table).where(
                    table.c.scope_id == self.scope_id,
                    table.c.node_id == node_id,
                    table.c.sequence == sequence,
                )
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        return {
            "digest": str(row["digest"]),
            "revision": int(row["revision"]),
            "available": bool(row["available"]),
            "reason": str(row["reason"]),
        }

    async def _last_sequence(self, connection: AsyncConnection, node_id: str) -> int | None:
        table = SOURCE_LIFECYCLE_EVENTS_TABLE
        row = (
            await connection.execute(
                select(table.c.sequence)
                .where(table.c.scope_id == self.scope_id, table.c.node_id == node_id)
                .order_by(table.c.sequence.desc())
                .limit(1)
            )
        ).one_or_none()
        return None if row is None else int(row[0])


def _replace_active(node: _LifecycleNode, active: bool, /) -> _LifecycleNode:
    return _LifecycleNode(
        node_id=node.node_id,
        kind=node.kind,
        policy=node.policy,
        parents=node.parents,
        revision=node.revision,
        active=active,
        binding_json=node.binding_json,
        artifact_id=node.artifact_id,
    )
