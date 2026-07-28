# SourceTrace

SourceTrace organizes private source material so every answer can be traced to immutable evidence.

## Language

**Knowledge Base**:
A named boundary that owns documents and limits which evidence may be retrieved together.
_Avoid_: Collection, workspace

**Document**:
A logical source inside one knowledge base, identified by its normalized file name and able to own multiple versions.
_Avoid_: File, upload

**Document Version**:
An immutable snapshot of a document's content, identified by its SHA-256 checksum and sequence number.
_Avoid_: Revision, copy

**Source File**:
The validated PDF bytes stored for a document version and addressed by a storage key outside the database.
_Avoid_: Database blob, parsed text

**Pending Version**:
An accepted document version whose source file is stored but whose asynchronous ingestion has not completed.
_Avoid_: Uploaded document, indexed version

**Latest Version**:
The document version with the greatest sequence number. Searchability is a separate lifecycle decision.
_Avoid_: Current file

**Duplicate Content**:
Content whose SHA-256 checksum already belongs to a document version in the same knowledge base, regardless of file name.
_Avoid_: Duplicate file name

**Ingestion Run**:
A replayable processing attempt for one document version, including parser and chunking configuration, progress, retry count, and a sanitized outcome.
_Avoid_: Redis job, background request

**Chunk**:
A stable, page-local token window derived from one document version and owned by an ingestion run.
_Avoid_: Paragraph, vector

**Searchable Version**:
A completed document version whose chunks all have validated dense embeddings and may participate in retrieval.
_Avoid_: Latest version, indexed file

**Active Searchable Version**:
The searchable version with the greatest version number for one document; newer incomplete versions do not replace it.
_Avoid_: Latest version, current upload

**Conversation**:
A durable question-history boundary that is permanently owned by one knowledge base and determines the scope for every future retrieval.
_Avoid_: Chat session, global conversation

**Question**:
An immutable user-authored prompt recorded inside one conversation; it is history, not evidence and not an answer run.
_Avoid_: Query result, message

**Answer Run**:
A replayable attempt to answer one question, recording its outcome and the model, prompt, retrieval, and workflow versions used.
_Avoid_: Assistant message, response

**Citation**:
A stable reference from a completed answer to an allowed chunk in an immutable document version, with page and excerpt metadata for inspection.
_Avoid_: Link, source name

**Refusal**:
A persisted answer-run outcome stating that evidence was insufficient or the generated text failed citation validation.
_Avoid_: Error, empty answer
