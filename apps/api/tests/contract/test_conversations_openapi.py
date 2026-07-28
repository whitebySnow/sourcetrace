from sourcetrace.main import create_app


def test_conversation_and_question_history_are_exposed_in_openapi() -> None:
    document = create_app().openapi()
    conversations = document["paths"][
        "/api/v1/knowledge-bases/{knowledge_base_id}/conversations"
    ]
    conversation = document["paths"][
        "/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}"
    ]
    questions = document["paths"][
        "/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
        "{conversation_id}/questions"
    ]

    assert set(conversations) >= {"get", "post"}
    assert "201" in conversations["post"]["responses"]
    assert "404" in conversations["post"]["responses"]
    assert "422" in conversations["post"]["responses"]
    assert "200" in conversations["get"]["responses"]
    assert "404" in conversations["get"]["responses"]
    assert "200" in conversation["get"]["responses"]
    assert "404" in conversation["get"]["responses"]
    assert "201" in questions["post"]["responses"]
    assert "404" in questions["post"]["responses"]
    assert "200" in questions["get"]["responses"]
    assert "404" in questions["get"]["responses"]

    conversation_list_parameters = {
        parameter["name"]: parameter
        for parameter in conversations["get"]["parameters"]
    }
    question_list_parameters = {
        parameter["name"]: parameter for parameter in questions["get"]["parameters"]
    }
    assert conversation_list_parameters["limit"]["schema"]["default"] == 20
    assert conversation_list_parameters["cursor"]["required"] is False
    assert question_list_parameters["limit"]["schema"]["default"] == 20
    assert question_list_parameters["cursor"]["required"] is False

    schemas = document["components"]["schemas"]
    assert set(schemas["ConversationResponse"]["required"]) >= {
        "id",
        "knowledge_base_id",
        "title",
        "created_at",
        "updated_at",
    }
    assert set(schemas["QuestionResponse"]["required"]) >= {
        "id",
        "conversation_id",
        "content",
        "created_at",
    }
