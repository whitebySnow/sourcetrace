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

**Latest Version**:
The document version with the greatest sequence number. Searchability is a separate lifecycle decision.
_Avoid_: Current file

**Duplicate Content**:
Content whose SHA-256 checksum already belongs to a document version in the same knowledge base, regardless of file name.
_Avoid_: Duplicate file name
