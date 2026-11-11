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
        page_texts = [page.extract_text() or "" for page in reader.pages]
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


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple:
    """Answer a question with exact citations of distinct evidence pages.

    Uses only the public scoped Memory API: full-text search first, then an
    exact entry lookup per hit to confirm the entry still carries its page
    Source. Each returned :class:`MemoryCitation` maps to a distinct page
    identity and at most ``limit`` citations are returned in relevance order.
    """

    from powercontext.artifacts.models import MemoryCitation
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    page = await memory.search(SearchMemoryRequest(query=question, limit=limit, mode="fts"))
    citations = []
    seen_pages: set[str] = set()
    for hit in page.hits:
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        identities = {
            reference.source_id
            for reference in entry.sources
            if reference.source_type == PDF_PAGE_SOURCE_NAME
        }
        fresh = identities - seen_pages
        if not fresh or len(identities) != 1:
            continue
        seen_pages |= fresh
        citations.append(citation)
        if len(citations) == limit:
            break
    return tuple(citations)
