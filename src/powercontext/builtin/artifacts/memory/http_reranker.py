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

"""HTTP scoring-service adapter for Memory reranking.

Implements the ``retrieval-01-reranking`` competition task: batched HTTP
score requests with strict response validation, cross-batch global stable
ordering, identity preservation, and exact request accounting.
"""

from __future__ import annotations

import math

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"


class HTTPMemoryReranker:
    """Rerank coarse Memory hits with one batched HTTP scoring service.

    Each candidate is sent to the service exactly once, in batches of at
    most ``batch_size``. Batch responses may arrive in any order but must
    cover every rank of their batch exactly once. Final selection is one
    global stable sort over the full candidate pool: score descending,
    original rank ascending on ties. Candidate identity (text, entry ids)
    is never rewritten.
    """

    policy_id: str

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = HTTP_RERANK_POLICY_ID,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        self.endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score the full candidate pool over batched HTTP requests."""

        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        requests_sent = 0
        for start in range(0, len(candidates), self._batch_size):
            batch_ranks = range(start + 1, start + self._batch_size + 1)
            batch_ranks = tuple(
                rank for rank in batch_ranks if rank <= len(candidates)
            )
            payload = {
                "query": query,
                "candidates": [
                    {"rank": rank, "text": candidates[rank - 1].text}
                    for rank in batch_ranks
                ],
            }
            response = await self._client.post(self.endpoint, json=payload)
            response.raise_for_status()
            scores.update(_parse_scores(response, expected_ranks=frozenset(batch_ranks)))
            requests_sent += 1
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        selected_ranks = tuple(rank for rank, _score in ordered[:limit])
        return MemoryRerankDecision(
            selected_ranks=selected_ranks,
            usage=InferenceUsage(requests=requests_sent),
        )


def _validate_bounds(candidate_count: int, limit: int) -> None:
    if candidate_count < 1:
        raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
    if limit < 1 or limit > candidate_count:
        raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003


def _parse_scores(
    response: httpx.Response,
    *,
    expected_ranks: frozenset[int],
) -> dict[int, float]:
    """Validate one batch response and map ranks to finite scores."""

    try:
        payload = response.json()
    except ValueError as error:
        raise ValueError("reranker response is not valid JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        raise ValueError("reranker response must contain exactly the 'scores' key")
    entries = payload["scores"]
    if not isinstance(entries, list):
        raise ValueError("reranker scores must be a list")
    parsed: dict[int, float] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"rank", "score"}:
            raise ValueError("each score record must contain exactly 'rank' and 'score'")
        rank = entry["rank"]
        score = entry["score"]
        if type(rank) is not int or rank not in expected_ranks or rank in parsed:
            raise ValueError("scores must cover each batch rank exactly once")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("score must be a finite number")
        if not math.isfinite(score):
            raise ValueError("score must be a finite number")
        parsed[rank] = float(score)
    if set(parsed) != expected_ranks:
        raise ValueError("scores must cover each batch rank exactly once")
    return parsed


__all__ = ["HTTP_RERANK_POLICY_ID", "HTTPMemoryReranker"]
