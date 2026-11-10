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

"""Row-level CSV capture: one captured Source per table row with faithful fields.

Each parsed row becomes an independent ``CAPTURED`` Source whose identity is
``csv:{document_id}:{primary_key_value}`` (both components percent-encoded), so
citations survive reordering and restarts. Field values, column order, and
header spelling (including surrounding whitespace) are preserved verbatim.
"""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]


class CSVDocumentInput(BaseModel):
    """One complete offline CSV document ready for row-level parsing."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One validated data row with original column order and raw values."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """A captured single-row Source with row identity and faithful fields."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def csv_row_source_id(document_id: str, primary_key_value: str, /) -> str:
    """Return the stable row identity, percent-encoding both components."""

    if not isinstance(document_id, str) or not document_id:
        raise ValueError("document_id must be a non-empty string")
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")
    if not isinstance(primary_key_value, str) or not primary_key_value:
        raise ValueError("primary_key_value must be a non-empty string")
    identity = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(
            f"row identity must not exceed {MAX_SOURCE_ID_LENGTH} characters, got {len(identity)}",
        )
    return identity


def parse_csv_document(value: CSVDocumentInput, /) -> tuple[CSVRowInput, ...]:
    """Parse one complete CSV document into validated rows, or fail atomically."""

    if not value.document_id or value.document_id != value.document_id.strip():
        raise ValueError("document_id must be non-empty without surrounding whitespace")
    if not value.primary_key:
        raise ValueError("primary_key must be non-empty")
    try:
        # ``utf-8-sig`` is strict UTF-8 that also tolerates a leading BOM.
        text = value.content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("document content must be valid UTF-8") from error

    try:
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise ValueError(f"malformed CSV document: {error}") from error

    if not records:
        raise ValueError("document must contain a header row")
    header = records[0]
    if not header or any(not field or not field.strip() for field in header):
        raise ValueError("header fields must be non-empty and not pure whitespace")
    if len(set(header)) != len(header):
        raise ValueError("header fields must be unique")
    try:
        key_index = header.index(value.primary_key)
    except ValueError:
        raise ValueError("primary_key must exactly match one header column") from None

    width = len(header)
    rows: list[CSVRowInput] = []
    seen_keys: set[str] = set()
    for record in records[1:]:
        if not record:
            continue  # completely blank physical record
        if len(record) != width:
            raise ValueError(f"data record width {len(record)} does not match header width {width}")
        key_value = record[key_index]
        if not key_value or not key_value.strip():
            raise ValueError("primary key value must not be empty or pure whitespace")
        if key_value in seen_keys:
            raise ValueError(f"duplicate primary key value: {key_value!r}")
        seen_keys.add(key_value)
        rows.append(
            CSVRowInput(
                document_id=value.document_id,
                primary_key=value.primary_key,
                primary_key_value=key_value,
                columns=tuple(header),
                values=tuple(record),
            ),
        )
    return tuple(rows)


class _CSVRowTextEvidenceProjection:
    """Standard ``powercontext.text-evidence`` projection for CSV rows."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type="csv-row",
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


class CSVRowSourceDefinition:
    """Bind the CSV row adapter to definition ``csv-row`` version ``1``."""

    name = "csv-row"
    version = "1"
    input_class = CSVRowInput
    source_class = CSVRowSource
    projections = (_CSVRowTextEvidenceProjection(),)

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            definition_version=self.version,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


CSV_ROW_SOURCE_DEFINITION: CSVRowSourceDefinition = CSVRowSourceDefinition()
