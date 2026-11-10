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

"""Cross-call embedding request coalescing with a bounded vector cache.

Concurrent ``embed`` calls that share texts reuse the same in-flight provider
request, and successful vectors are kept in a bounded LRU cache so later calls
can skip the provider entirely. Cancelling one waiter never cancels work other
waiters depend on, and every caller still receives vectors in its own original
input order with the usage of the distinct provider batches it waited for.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
    from powercontext.builtin.inference.protocols import EmbeddingModel

BatchTexts = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _BatchReport:
    """Portable usage reported by exactly one underlying provider batch."""

    requests: int
    input_tokens: int | None
    output_tokens: int | None


_Outcome = tuple[EmbeddingVector, _BatchReport]


def _discard_outcome(future: asyncio.Future[_Outcome]) -> None:
    """Mark unexpected outcomes as retrieved so cancelled waiters stay quiet."""

    if future.cancelled():
        return
    future.exception()


class CoalescingEmbeddingModel:
    """Share in-flight embedding work across calls and cache successful vectors."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if max_batch_size < 1:
            raise ValueError("max_batch_size must be at least 1")
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms must not be negative")
        if cache_size < 0:
            raise ValueError("cache_size must not be negative")
        self.profile: EmbeddingProfile = model.profile
        self._model = model
        self._max_batch_size = max_batch_size
        self._window = coalesce_ms / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._pending: dict[str, asyncio.Future[_Outcome]] = {}
        self._queue: list[str] = []
        self._flush_task: asyncio.Task[None] | None = None

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return vectors in the caller's original order, merging shared work."""

        ordered = tuple(texts)
        if not ordered:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        loop = asyncio.get_running_loop()
        cached: dict[str, EmbeddingVector] = {}
        futures: dict[str, asyncio.Future[_Outcome]] = {}
        for text in dict.fromkeys(ordered):
            vector = self._cache_lookup(text)
            if vector is not None:
                cached[text] = vector
                continue
            future = self._pending.get(text)
            if future is None:
                future = loop.create_future()
                future.add_done_callback(_discard_outcome)
                self._pending[text] = future
                self._queue.append(text)
            futures[text] = future
        if self._queue:
            self._ensure_flush(loop)
        if futures:
            # Shielding keeps shared provider work alive when this caller is cancelled.
            await asyncio.shield(asyncio.gather(*futures.values()))

        vectors: list[EmbeddingVector] = []
        reports: list[_BatchReport] = []
        seen_reports: set[int] = set()
        for text in ordered:
            if text in cached:
                vectors.append(cached[text])
                continue
            vector, report = futures[text].result()
            vectors.append(vector)
            if id(report) not in seen_reports:
                seen_reports.add(id(report))
                reports.append(report)
        return EmbeddingResult(vectors=tuple(vectors), usage=_merge_usage(reports))

    # ------------------------------------------------------------------ batching

    def _ensure_flush(self, loop: asyncio.AbstractEventLoop) -> None:
        task = self._flush_task
        if task is not None and not task.done():
            return
        self._flush_task = loop.create_task(self._flush())

    async def _flush(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while self._queue:
                # A zero window still yields once so same-tick callers can join.
                await asyncio.sleep(self._window if self._window > 0 else 0)
                while self._queue:
                    batch = tuple(self._queue[: self._max_batch_size])
                    del self._queue[: self._max_batch_size]
                    loop.create_task(self._run_batch(batch))
        finally:
            self._flush_task = None

    async def _run_batch(self, batch: BatchTexts) -> None:
        try:
            result = await self._model.embed(batch)
            vectors = tuple(result.vectors)
            if len(vectors) != len(batch):
                raise ValueError(
                    f"embedding model returned {len(vectors)} vectors for {len(batch)} texts",
                )
            usage = result.usage
        except asyncio.CancelledError as error:
            self._settle_failure(batch, error)
            raise
        except BaseException as error:  # noqa: BLE001 - failures flow to every waiter
            self._settle_failure(batch, error)
            return
        report = _BatchReport(
            requests=usage.requests,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        for text, vector in zip(batch, vectors):
            self._cache_store(text, vector)
            self._settle_success(text, (vector, report))

    def _settle_success(self, text: str, outcome: _Outcome) -> None:
        future = self._pending.pop(text, None)
        if future is not None and not future.done():
            future.set_result(outcome)

    def _settle_failure(self, batch: BatchTexts, error: BaseException) -> None:
        for text in batch:
            future = self._pending.pop(text, None)
            if future is not None and not future.done():
                future.set_exception(error)

    # --------------------------------------------------------------------- cache

    def _cache_lookup(self, text: str) -> EmbeddingVector | None:
        if self._cache_size == 0:
            return None
        vector = self._cache.get(text)
        if vector is None:
            return None
        self._cache.move_to_end(text)
        return vector

    def _cache_store(self, text: str, vector: EmbeddingVector) -> None:
        if self._cache_size == 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


def _merge_usage(reports: list[_BatchReport]) -> InferenceUsage:
    requests = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    for report in reports:
        requests += report.requests
        if report.input_tokens is not None:
            input_tokens = report.input_tokens if input_tokens is None else input_tokens + report.input_tokens
        if report.output_tokens is not None:
            output_tokens = report.output_tokens if output_tokens is None else output_tokens + report.output_tokens
    return InferenceUsage(requests=requests, input_tokens=input_tokens, output_tokens=output_tokens)
