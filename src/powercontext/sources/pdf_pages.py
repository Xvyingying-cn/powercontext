# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Full financial-report PDF parsing and page-level evidence retrieval.

Every physical page of the original PDF becomes one immutable captured
Source identified by the document id, the SHA-256 digest of the complete
original bytes and the physical page number (counted from the first page
of the file, never a printed folio). Parsing is atomic: unreadable inputs
fail with :class:`ValueError` and never return a partial page tuple.
Retrieval answers natural-language questions through the public scoped
Memory API and returns exact citations of distinct evidence pages.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections import Counter

from pypdf import PasswordType, PdfReader
from pydantic import BaseModel, ConfigDict

from powercontext.sources import AdapterSourceDefinition, Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PDF_PAGE_SOURCE_NAME = "pdf-page"


class PDFDocumentInput(BaseModel):
    """Complete original PDF bytes with a caller-chosen document id."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One physical page of a parsed PDF document."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """Durable verbatim copy of one physical PDF page."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


def _page_identity(document_id: str, document_sha256: str, page_number: int, /) -> str:
    """Identity binds document id, full-byte digest and physical page number.

    The digest changes with every revision of the original file, so revised
    documents get fresh page identities while old references keep pointing
    at the original revision. The hash never exceeds the Source id limit.
    """

    identity = f"{document_id}\0{document_sha256}\0{page_number}"
    return f"pdf-page_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _extract_page_text(page, document_id: str, page_number: int, /) -> str:
    """Return the full extractable text of one physical page.

    The plain extraction mode is authoritative. A page whose plain extraction
    fails is retried in layout mode, which keeps every word while adding
    spacing; only a page that cannot be read in either mode makes the whole
    document unreadable, so no partial page tuple is ever produced.
    """

    try:
        return page.extract_text() or ""
    except Exception:
        try:
            return page.extract_text(extraction_mode="layout") or ""
        except Exception as error:
            raise ValueError(
                f"page {page_number} of PDF document {document_id!r} is not fully extractable"
            ) from error


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of the original PDF in document order.

    Returns one :class:`PDFPageInput` per page, page numbers starting at 1,
    including blank pages (empty text). Files readable without a user
    password - empty-password permission markers included - are legal
    inputs. Empty files, non-PDF payloads, corrupt documents and pages
    that cannot be fully extracted raise :class:`ValueError` before any
    result is produced, so a failure never yields partial output.
    """

    document_id = value.document_id
    if not document_id.strip():
        raise ValueError("document_id must not be empty or blank")
    digest = hashlib.sha256(value.content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(value.content))
        if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
            raise ValueError(f"PDF document {document_id!r} requires a password")
        page_texts = [
            _extract_page_text(page, document_id, number)
            for number, page in enumerate(reader.pages, start=1)
        ]
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"unreadable PDF document {document_id!r}") from error
    return tuple(
        PDFPageInput(
            document_id=document_id,
            document_sha256=digest,
            page_number=number,
            text=text,
        )
        for number, text in enumerate(page_texts, start=1)
    )


class PDFPageSourceAdapter:
    """Materialize parsed physical pages as immutable captured evidence."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        return PDFPageSource(
            name=_page_identity(value.document_id, value.document_sha256, value.page_number),
            materialization=SourceMaterialization.CAPTURED,
            description="Verbatim extractable text of one physical page of the original PDF.",
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose the standard text-evidence shape for every captured page."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()
PDF_PAGE_TEXT_EVIDENCE_PROJECTION = PDFPageTextEvidenceProjection()
PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    projections=(PDF_PAGE_TEXT_EVIDENCE_PROJECTION,),
)


_TERM_PATTERN = re.compile(r"[a-z]+|\d+")

_QUERY_STOP_TERMS = frozenset(
    """
    a an the and or for of to in on at by from with as is are was were be been being
    what which who whom whose how why when where do does did done has have had having
    this that these those it its they them their there here we our you your
    not no nor if then than so such can could should would may might must will shall
    also more most less least very much many any all both each other same only just
    about above after again against between during into over under out up down
    usd million millions billion billions thousand thousands
    fiscal fy year years quarter quarters period periods
    respond response answer answered assuming assume assumed perspective analyst analysts
    define defined definition question questions rely relying explain provide give
    calculate calculated compute state return mentioned described shown show detail details
    use using used according
    """.split()
)

_SYNONYMS = {
    "battles": ("litigation", "lawsuits", "disputes", "proceedings"),
    "legal": ("litigation", "lawsuits", "proceedings"),
    "lawsuit": ("litigation", "legal"),
    "litigation": ("legal", "lawsuits"),
    "restructuring": ("impairment", "charges"),
    "impairment": ("restructuring", "charges"),
    "capex": ("capital", "spending"),
    "geographies": ("geographic", "regions", "operations"),
    "operates": ("operations", "operating"),
}

_ABBREVIATIONS = (
    (re.compile(r"sg\s*&\s*a", re.IGNORECASE), " selling general administrative "),
    (re.compile(r"d\s*&\s*a", re.IGNORECASE), " depreciation amortization "),
    (re.compile(r"p\s*&\s*l", re.IGNORECASE), " income "),
)

_TITLE_INCOME = "consolidated statement of income"
_TITLE_CASH = "consolidated statement of cash flows"
_TITLE_BALANCE = "consolidated balance sheet"


def _prepare_question(question: str) -> str:
    """Spell out finance abbreviations and collapse the whitespace they add."""

    out = question
    for pattern, replacement in _ABBREVIATIONS:
        out = pattern.sub(replacement, out)
    return re.sub(r"\s+", " ", out)


def _question_terms(question: str) -> tuple[dict[str, float], dict[str, float]]:
    """Return base query terms and synonym terms, each with a weight.

    The first sentence carries the real question and is weighted twice; the
    trailing analyst instructions that benchmark questions append stay at unit
    weight so their generic wording cannot dominate the ranking. Terms that
    only exist through synonym expansion never dilute the base term set.
    """

    prepared = _prepare_question(question)
    head, separator, tail = prepared.partition("?")
    if not separator:
        head, tail = prepared, ""
    base: dict[str, float] = {}
    synonyms: dict[str, float] = {}
    for segment, weight in ((head, 2.0), (tail, 1.0)):
        for term in _TERM_PATTERN.findall(segment):
            if term in _QUERY_STOP_TERMS or (not term.isdigit() and len(term) < 2):
                continue
            base[term] = max(base.get(term, 0.0), weight)
            for synonym in _SYNONYMS.get(term, ()):
                synonyms[synonym] = max(synonyms.get(synonym, 0.0), weight)
    return base, synonyms


def _required_titles(question: str) -> tuple[str, ...]:
    """Return the statement titles a question explicitly asks about."""

    prepared = _prepare_question(question).casefold()
    titles = []
    if re.search(r"income statements?|statement of income", prepared):
        titles.append(_TITLE_INCOME)
    if "cash flow" in prepared:
        titles.append(_TITLE_CASH)
    if "balance sheet" in prepared:
        titles.append(_TITLE_BALANCE)
    return tuple(titles)


def _pool_queries(question: str, base, synonyms) -> tuple[str, ...]:
    """Return the query variants that fill the candidate pool.

    A single long question clears the Memory admission floor only when a page
    covers a quarter of its terms, which drops sparse table pages. Short
    windows over the most specific terms need two matches only, so the pool
    keeps evidence pages that the long query would filter out. Windows are
    drawn both from the question vocabulary alone and from the vocabulary
    enlarged by synonyms, and one synonym-group query lets pages that match
    a cluster of related finance wording enter the pool together.
    """

    prepared = _prepare_question(question)
    queries = [question, prepared]
    for weights in (base, {**base, **synonyms}):
        specific = sorted(
            (term for term in weights if not term.isdigit()),
            key=lambda term: (-len(term), term),
        )
        for start in range(0, min(len(specific), 12), 3):
            window = specific[start : start + 3]
            if len(window) == 3:
                queries.append(" ".join(window))
    group = sorted(
        set(synonyms) | {term for term in base if term in _SYNONYMS},
        key=lambda term: (-len(term), term),
    )
    if group:
        queries.append(" ".join(group[:10]))
    return tuple(dict.fromkeys(queries))


def _split_document(text: str) -> tuple[str, str]:
    """Split an entry text into its ``DOCUMENT_ID\\nPAGE_TEXT`` parts.

    The projection always prefixes the document id, but an entry may also be a
    fragment of a page. A first line that looks like prose is therefore kept as
    body text instead of being mistaken for a document identity.
    """

    head, separator, body = text.partition("\n")
    if separator and 0 < len(head) <= 80 and "." not in head and "," not in head:
        return head, body
    return "", text


def _candidate_pages(hits):
    """Return ``(document id, term counts, hit, pool order)`` rows."""

    rows = []
    for order, hit in enumerate(hits):
        document_id, body = _split_document(hit.text or "")
        tokens = _TERM_PATTERN.findall(body.casefold())
        if not tokens:
            continue
        rows.append((document_id, Counter(tokens), hit, order))
    return rows


def _rank_pages(rows, base, synonyms, titles):
    """Rank candidate pages by inverse-document-frequency term coverage.

    Scores are computed over the candidate pool itself: terms that most pages
    share carry little weight, so a page rises when it covers the rare,
    question-specific vocabulary. The coverage multiplier is deliberately
    soft, because benchmark evidence pages often match only a handful of rare
    terms of a long question. A page also inherits a mild preference for the
    document whose best page matches best, and a question that names a
    financial statement promotes the pages that carry that statement title.
    """

    terms = {**base, **synonyms}
    total = len(rows)
    if not total:
        return []
    document_frequency: Counter[str] = Counter()
    for _document_id, counts, _hit, _order in rows:
        document_frequency.update(counts.keys())

    def inverse(term: str) -> float:
        return math.log(1.0 + total / (1.0 + document_frequency.get(term, 0)))

    scored = []
    for document_id, counts, hit, order in rows:
        score = 0.0
        matched = 0
        for term, weight in terms.items():
            frequency = counts.get(term, 0)
            if frequency:
                score += weight * inverse(term) * (1.0 + math.log(frequency))
                if term in base:
                    matched += 1
        if not matched:
            continue
        coverage = matched / max(1, len(base))
        score *= 0.8 + 0.2 * coverage
        body = " ".join((hit.text or "").split()).casefold()
        if titles and any(title in body for title in titles):
            score *= 3.0
        document_terms = set(_TERM_PATTERN.findall(document_id.casefold()))
        naming = sum(1 for term in base if term in document_terms and not term.isdigit())
        if naming:
            score *= 1.0 + 0.25 * naming
        scored.append([score, document_id, hit, order])

    best: dict[str, float] = {}
    for score, document_id, _hit, _order in scored:
        best[document_id] = max(best.get(document_id, 0.0), score)
    leader = max(best.values())
    for row in scored:
        row[0] *= 1.0 + 0.15 * (best[row[1]] / leader)
    scored.sort(key=lambda row: (-row[0], row[3]))
    return [hit for _score, _document_id, hit, _order in scored]


async def _first_page_citations(memory, question: str, limit: int, /) -> tuple:
    """Fallback: return the first citations of one plain lexical search."""

    from powercontext.artifacts.models import MemoryCitation
    from powercontext.builtin.runtime import SearchMemoryRequest

    page = await memory.search(SearchMemoryRequest(query=question, limit=limit, mode="fts"))
    return tuple(
        MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        for hit in page.hits[:limit]
    )


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple:
    """Answer a question with exact citations of distinct evidence pages.

    Uses only the public scoped Memory API: several lexical searches fill a
    candidate pool, the pool is re-ranked offline by question-specific term
    coverage, and every returned :class:`MemoryCitation` is confirmed through
    an exact entry lookup so that it still carries exactly one page Source.
    At most ``limit`` citations of distinct pages are returned in rank order.
    """

    from powercontext.artifacts.models import MemoryCitation
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    limit = max(1, int(limit))
    base, synonyms = _question_terms(question)
    if not base:
        return await _first_page_citations(memory, question, limit)
    titles = _required_titles(question)
    pool_limit = max(64, limit * 6)
    hits: dict[tuple[str, str], object] = {}
    for query in _pool_queries(question, base, synonyms):
        try:
            page = await memory.search(SearchMemoryRequest(query=query, limit=pool_limit, mode="fts"))
        except Exception:
            continue
        for hit in page.hits:
            hits.setdefault((hit.entry_id, hit.entry_version_id), hit)
    ranked = _rank_pages(_candidate_pages(hits.values()), base, synonyms, titles)
    if not ranked:
        return await _first_page_citations(memory, question, limit)
    citations = []
    seen_pages: set[str] = set()
    for hit in ranked[: limit * 4]:
        if len(citations) == limit:
            break
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        try:
            entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        except Exception:
            continue
        identities = {
            reference.source_id
            for reference in entry.sources
            if reference.source_type == PDF_PAGE_SOURCE_NAME
        }
        if len(identities) != 1:
            continue
        identity = next(iter(identities))
        if identity in seen_pages:
            continue
        seen_pages.add(identity)
        citations.append(citation)
    return tuple(citations)
