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

**Retrieval Query**:
The standalone query actually embedded for one answer run, derived from the current question and a bounded list of recent user questions. It is replay metadata, not evidence.
_Avoid_: Previous answer, conversation transcript

**Evidence Decision**:
A structured Agent decision declaring whether retrieved chunks are sufficient and identifying the exact chunk IDs allowed to reach answer generation.
_Avoid_: Model reasoning, relevance score

**Supplemental Retrieval**:
The single optional second retrieval stage allowed after an insufficient Evidence Decision, using up to the remaining two-query budget as independent standalone Retrieval Queries against the same Knowledge Base.
_Avoid_: Retry loop, web search

**Citation Repair**:
The single allowed attempt to revise an invalid draft using only the selected evidence and its existing citation labels before deterministic validation runs again.
_Avoid_: Answer regeneration, citation fabrication

**Answer Run**:
A replayable attempt to answer one question, recording its lifecycle state, outcome, retrieval query, and the model, generation prompt, query-rewrite prompt, evidence-assessment prompt, citation-repair prompt, retrieval, and workflow versions used. At most one run may be active per conversation.
_Avoid_: Assistant message, response

**Cancellation**:
A durable request to stop an active answer run; any streamed draft is discarded and only the terminal cancelled state is retained.
_Avoid_: Failed answer, partial answer

**Citation**:
A stable reference from a completed answer to an allowed chunk in an immutable document version, with page and excerpt metadata for inspection.
_Avoid_: Link, source name

**Refusal**:
A persisted answer-run outcome stating that evidence was insufficient or the generated text failed citation validation.
_Avoid_: Error, empty answer

**Evaluation Dataset**:
A versioned set of questions, answer or refusal expectations, and an immutable document-version snapshot used to exercise the evaluation contract. Its review status distinguishes tooling fixtures from reviewed evidence.
_Avoid_: Demo prompts, test log

**Reviewed Evaluation Dataset**:
An Evaluation Dataset whose expectations and evidence references were checked by an identified human reviewer at a recorded UTC time and may be used in a real-provider evaluation.
_Avoid_: Tooling fixture, generated benchmark

**Evaluation Fixture**:
A deterministic synthetic Evaluation Dataset and independent observations used only to test the harness; it is never a reviewed dataset or a source of product metrics.
_Avoid_: Benchmark result, reviewed sample

**Evaluation Observation**:
The answer outcome, retrieved evidence, and final citations produced for one evaluation case before comparison with its expected result.
_Avoid_: Ground truth, score

**Evaluation Report**:
A replayable artifact that keeps retrieval, citation, refusal, and end-to-end results separate and binds them to dataset, code, model, workflow, chunking, embedding, and retrieval versions.
_Avoid_: Accuracy claim, benchmark without provenance

**Citation Diagnostics Report**:
A sanitized, replayable classification of failed answer citations, bound to one Evaluation Dataset and one Evaluation Report without retaining questions, answers, prompts, or evidence text.
_Avoid_: Alternative-evidence approval, corrected evaluation result

**Evidence Assessment Diagnostics Report**:
A sanitized, replayable classification of answerable refusals at the Evidence Decision stage, bound to one Evaluation Dataset and one Evaluation Report without retaining questions, answers, queries, prompts, or evidence text.
_Avoid_: Evidence sufficiency override, corrected evaluation result
