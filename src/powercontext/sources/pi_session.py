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

"""Pi session branch capture: parse the selected branch, project raw visible
events, and run incremental capture with durable checkpoints.

The parser accepts Pi v3 JSONL session archives, resolves the ancestor chain of
the selected leaf node, and emits one immutable :class:`PiEventInput` per
visible event in root-to-leaf order. Compaction and branch summaries never
erase earlier evidence: they are captured as events themselves and every prior
visible event keeps its complete original record. The Source stores each event
verbatim so large tool results remain fully retrievable regardless of Memory
entry limits.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue

from powercontext.sources import AdapterSourceDefinition, Source, SourceMaterialization
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_CONNECTOR_VERSION = "1"


class PiEventInput(BaseModel):
    """One raw visible event on the selected Pi session branch."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: JsonValue


class PiSessionInput(BaseModel):
    """Raw Pi v3 session archive bytes plus an optional selected leaf node."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    content: bytes
    leaf_id: str | None = None


PiEventInput.model_rebuild()
PiSessionInput.model_rebuild()


def _compact_json(value: object, /) -> str:
    """Serialize with sorted keys, preserved Unicode and no added whitespace."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _text_blocks(content: object, /) -> str:
    """Join visible text blocks with newlines; non-text blocks are ignored."""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _message_body(message: dict[str, Any], /) -> str | None:
    """Build the visible body for a plain message with text and tool calls."""

    content = message.get("content")
    if isinstance(content, str):
        return content if content else None
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        elif block_type == "toolCall":
            name = block.get("name")
            call_id = block.get("id")
            arguments = block.get("arguments")
            parts.append(
                f"[tool_call {name} {call_id}] {_compact_json(arguments if arguments is not None else {})}"
            )
        # thinking, image and other blocks never become visible text.
    return "\n".join(parts) if parts else None


def _tool_result_body(message: dict[str, Any], /) -> str | None:
    """Build ``[tool_result TOOL_NAME TOOL_CALL_ID] TEXT`` bodies."""

    tool_name = message.get("toolName")
    tool_call_id = message.get("toolCallId")
    text = _text_blocks(message.get("content"))
    return f"[tool_result {tool_name} {tool_call_id}] {text}"


def _bash_body(message: dict[str, Any], /) -> str | None:
    """Build ``$ COMMAND`` followed by a newline and the captured output."""

    command = message.get("command")
    output = message.get("output")
    return f"$ {command}\n{output}"


def _event_body(event_type: str, record: dict[str, Any], /) -> tuple[str, str] | None:
    """Return ``(role, body)`` for one visible event, or ``None`` for control nodes."""

    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        role = message.get("role")
        if role == "toolResult":
            body = _tool_result_body(message)
        elif role == "bashExecution":
            body = _bash_body(message)
        else:
            body = _message_body(message)
        if body is None or body == "":
            return None
        return str(role), body
    if event_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        if summary is None or summary == "":
            return None
        return event_type, str(summary)
    if event_type == "custom_message":
        body = _message_body({"content": record.get("content")})
        if body is None or body == "":
            return None
        return "custom", body
    # model_change, thinking_level_change and any other control node stay on
    # the graph but never produce text evidence.
    return None


def parse_pi_session(value: PiSessionInput, /) -> tuple[PiEventInput, ...]:
    """Parse the archive and return the selected branch's visible events, root first.

    Raises :class:`ValueError` for invalid encodings, malformed JSON, duplicate
    identifiers, missing or forward-referencing parents, cycles, missing leaves
    and invalid session headers. The whole input is validated before any event
    is returned, so failures never produce partial results.
    """

    try:
        text = value.content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session archive must be valid UTF-8") from error
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("Pi session archive contains invalid JSON") from error
        if not isinstance(record, dict):
            raise ValueError("Pi session records must be JSON objects")
        records.append(record)
    if not records:
        raise ValueError("Pi session archive is empty")
    header = records[0]
    if header.get("type") != "session" or header.get("version") != 3:
        raise ValueError("Pi session archive must start with a version 3 session header")
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Pi session header requires a non-empty id")
    order: dict[str, int] = {}
    for index, record in enumerate(records[1:], start=1):
        node_id = record.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("Pi session records require non-empty ids")
        if node_id in order:
            raise ValueError(f"Pi session record id {node_id!r} is duplicated")
        if "parentId" not in record:
            raise ValueError(f"Pi session record {node_id!r} is missing parentId")
        order[node_id] = index
    leaf_id = value.leaf_id if value.leaf_id is not None else records[-1].get("id")
    if not isinstance(leaf_id, str) or leaf_id not in order:
        raise ValueError(f"Pi session leaf {leaf_id!r} is missing from the archive")
    chain: list[str] = []
    node: str | None = leaf_id
    visited: set[str] = set()
    while isinstance(node, str) and node not in visited:
        visited.add(node)
        chain.append(node)
        parent = records[order[node]].get("parentId")
        if parent is None:
            node = None
            break
        if not isinstance(parent, str) or parent not in order or order[parent] >= order[node]:
            raise ValueError(f"Pi session record {node!r} has an invalid parent reference")
        node = parent
    if node is not None:
        raise ValueError("Pi session branch contains a parent cycle")
    events: list[PiEventInput] = []
    for event_id in reversed(chain):
        record = records[order[event_id]]
        event_type = record.get("type")
        if not isinstance(event_type, str):
            raise ValueError(f"Pi session record {event_id!r} is missing its type")
        visible = _event_body(event_type, record)
        if visible is None:
            continue
        role, body = visible
        parent_id = record.get("parentId")
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=event_id,
                parent_id=parent_id if isinstance(parent_id, str) else None,
                event_type=event_type,
                role=role,
                content=f"{role}: {body}",
                record=record,
            )
        )
    return tuple(events)


class PiEventSource(Source):
    """Durable verbatim copy of one captured Pi session event."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: JsonValue


def _event_identity(session_id: str, event_id: str, /) -> str:
    """Identity depends only on session and event ids and never collides."""

    identity = f"{session_id}\0{event_id}"
    return f"pi-event_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _record_digest(record: dict[str, Any], /) -> str:
    """Stable payload digest used by checkpoints to detect history rewrites."""

    return hashlib.sha256(_compact_json(record).encode("utf-8")).hexdigest()


class PiEventSourceAdapter:
    """Materialize parsed Pi events as immutable captured evidence."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        return PiEventSource(
            name=_event_identity(value.session_id, value.event_id),
            materialization=SourceMaterialization.CAPTURED,
            description="Raw visible event captured from a Pi session branch.",
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose the standard text-evidence shape for every captured event."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_ADAPTER = PiEventSourceAdapter()
PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PI_EVENT_SOURCE_ADAPTER,
    projections=(PiEventTextEvidenceProjection(),),
)


class PiSessionConnector:
    """Incrementally capture the selected branch with durable checkpoints.

    Every run fully parses the archive first; only then are new events
    submitted and the checkpoint proposal formed. Rewritten history and broken
    inputs raise before any submission, so committed checkpoints survive
    failures untouched and repaired inputs resume from the original progress.
    """

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: Path | str, leaf_id: str | None = None, /) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        raw = self._path.read_bytes()
        events = parse_pi_session(PiSessionInput(content=raw, leaf_id=self._leaf_id))
        stored = session.checkpoint.get("events") if isinstance(session.checkpoint, dict) else None
        seen: dict[str, str] = {}
        if isinstance(stored, dict):
            seen = {str(key): str(digest) for key, digest in stored.items()}
        for event in events:
            known = seen.get(event.event_id)
            if known is not None and known != _record_digest(event.record):
                raise ValueError(f"captured Pi event {event.event_id!r} was rewritten")
        for event in events:
            if event.event_id in seen:
                continue
            await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
            seen[event.event_id] = _record_digest(event.record)
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"events": seen},
        )


__all__ = [
    "PI_EVENT_SOURCE_ADAPTER",
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_SESSION_CONNECTOR_NAME",
    "PI_SESSION_CONNECTOR_VERSION",
    "PiEventInput",
    "PiEventSource",
    "PiEventSourceAdapter",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
]
