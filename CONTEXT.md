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
