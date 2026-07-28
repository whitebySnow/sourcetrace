from sourcetrace.main import create_app


def test_answer_stream_history_and_source_are_exposed_in_openapi() -> None:
    document = create_app().openapi()
    answers = document["paths"][
        "/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
        "{conversation_id}/answers"
    ]
    source = document["paths"][
        "/api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/"
        "versions/{version_id}/source"
    ]["get"]

    stream_schema = answers["post"]["responses"]["200"]["content"][
        "text/event-stream"
    ]["schema"]
    assert stream_schema["$ref"] == "#/components/schemas/AnswerEvent"
    assert answers["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"] == "#/components/schemas/AnswerHistoryResponse"
    assert answers["post"]["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ] == "#/components/schemas/AnswerRequest"

    event = document["components"]["schemas"]["AnswerEvent"]
    assert event["discriminator"]["propertyName"] == "type"
    assert len(event["oneOf"]) == 5
    assert set(document["components"]["schemas"]["CitationResponse"]["required"]) >= {
        "id",
        "document_id",
        "document_version_id",
        "document_name",
        "page_number",
        "excerpt",
        "source_url",
    }
    assert source["responses"]["200"]["content"]["application/pdf"]["schema"] == {
        "type": "string",
        "format": "binary",
    }
