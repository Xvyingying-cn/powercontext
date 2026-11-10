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

"""Incremental Context Pack wire format for prepared context transfer.

First transfer sends a complete pack; later transfers send a delta that keeps
a prefix of the previous pack and appends the target suffix. Receivers rebuild
the target pack from serialized bytes only, without sender-side caches.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Iterable

CONTEXT_PACK_SCHEMA = "powercontext.context-pack.v1"
CONTEXT_PACK_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES = 512


class ContextPackError(ValueError):
    """Protocol or chunking violation for Context Pack wire data."""


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(values: object) -> tuple[bytes, ...]:
    if not isinstance(values, list):
        raise ContextPackError("items must be a list of base64 strings")
    decoded: list[bytes] = []
    for value in values:
        if not isinstance(value, str):
            raise ContextPackError("items must be a list of base64 strings")
        try:
            decoded.append(base64.b64decode(value, validate=True))
        except (binascii.Error, ValueError) as error:
            raise ContextPackError("item is not valid base64") from error
    return tuple(decoded)


def _load_object(data: bytes, *, required: frozenset[str], kind: str) -> dict[str, object]:
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContextPackError(f"{kind} is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ContextPackError(f"{kind} must be a JSON object")
    if set(payload) != set(required):
        raise ContextPackError(f"{kind} must contain exactly {sorted(required)}")
    return payload


def _chunk_utf8(content: str, chunk_bytes: int) -> tuple[bytes, ...]:
    if chunk_bytes < 1:
        raise ContextPackError("chunk_bytes must be a positive integer")
    if not content:
        return ()
    chunks: list[bytes] = []
    current = bytearray()
    for char in content:
        encoded = char.encode("utf-8")
        if len(encoded) > chunk_bytes:
            raise ContextPackError("chunk_bytes cannot hold one UTF-8 character")
        if len(current) + len(encoded) > chunk_bytes:
            chunks.append(bytes(current))
            current.clear()
        current.extend(encoded)
    if current:
        chunks.append(bytes(current))
    return tuple(chunks)


def _sized_pack(items: tuple[bytes, ...], digest: str, unsigned: dict[str, object]) -> ContextPack:
    size = 0
    for _ in range(8):
        encoded = _canonical_json({**unsigned, "digest": digest, "byte_count": size})
        if len(encoded) == size:
            return ContextPack(items=items, digest=digest, byte_count=size)
        size = len(encoded)
    raise ContextPackError("context pack byte count does not converge")


@dataclass(frozen=True, slots=True)
class ContextPack:
    """Complete context transfer unit with chunk identity and integrity data."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: Iterable[bytes]) -> ContextPack:
        packed = tuple(bytes(item) for item in items)
        unsigned: dict[str, object] = {
            "schema": CONTEXT_PACK_SCHEMA,
            "items": _encode_items(packed),
        }
        digest = "sha256:" + hashlib.sha256(_canonical_json(unsigned)).hexdigest()
        return _sized_pack(packed, digest, unsigned)

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        return cls.build(_chunk_utf8(content, chunk_bytes))

    def to_bytes(self) -> bytes:
        return _canonical_json(
            {
                "schema": CONTEXT_PACK_SCHEMA,
                "items": _encode_items(self.items),
                "digest": self.digest,
                "byte_count": self.byte_count,
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        payload = _load_object(
            data,
            required=frozenset({"schema", "items", "digest", "byte_count"}),
            kind="context pack",
        )
        if payload["schema"] != CONTEXT_PACK_SCHEMA:
            raise ContextPackError("unsupported context pack schema")
        if isinstance(payload["byte_count"], bool) or not isinstance(payload["byte_count"], int):
            raise ContextPackError("context pack byte_count must be an integer")
        rebuilt = cls.build(_decode_items(payload["items"]))
        if payload["digest"] != rebuilt.digest:
            raise ContextPackError("context pack digest mismatch")
        if payload["byte_count"] != len(data):
            raise ContextPackError("context pack byte_count mismatch")
        return rebuilt


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """Reusable prefix of one base pack plus the target suffix."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        prefix = 0
        for left, right in zip(base.items, target.items):
            if left != right:
                break
            prefix += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=target.items[prefix:],
        )

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        return _canonical_json(
            {
                "schema": CONTEXT_PACK_DELTA_SCHEMA,
                "base_digest": self.base_digest,
                "target_digest": self.target_digest,
                "prefix_items": self.prefix_items,
                "appended": _encode_items(self.appended),
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        payload = _load_object(
            data,
            required=frozenset(
                {"schema", "base_digest", "target_digest", "prefix_items", "appended"}
            ),
            kind="context pack delta",
        )
        if payload["schema"] != CONTEXT_PACK_DELTA_SCHEMA:
            raise ContextPackError("unsupported context pack delta schema")
        prefix = payload["prefix_items"]
        if isinstance(prefix, bool) or not isinstance(prefix, int) or prefix < 0:
            raise ContextPackError("delta prefix_items must be a non-negative integer")
        base_digest = payload["base_digest"]
        target_digest = payload["target_digest"]
        if not isinstance(base_digest, str) or not isinstance(target_digest, str):
            raise ContextPackError("delta digests must be strings")
        return cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix,
            appended=_decode_items(payload["appended"]),
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Rebuild the target pack from a base pack and one validated delta."""

    if base.digest != delta.base_digest:
        raise ContextPackError("delta base digest does not match the supplied pack")
    if delta.prefix_items < 0 or delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix_items is outside the base pack")
    target = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("delta target digest does not match the rebuilt pack")
    return target


__all__ = [
    "CONTEXT_PACK_DELTA_SCHEMA",
    "CONTEXT_PACK_SCHEMA",
    "DEFAULT_CHUNK_BYTES",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
