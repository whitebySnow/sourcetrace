from datetime import datetime
from uuid import UUID

from httpx import AsyncClient


async def _create_knowledge_base(client: AsyncClient, name: str = "Agent Papers") -> str:
    response = await client.post("/api/v1/knowledge-bases", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


async def test_user_can_create_and_retrieve_a_conversation_in_a_knowledge_base(
    client: AsyncClient,
) -> None:
    knowledge_base_id = await _create_knowledge_base(client)

    create_response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
        json={"title": "  BGE-M3 notes  "},
    )

    assert create_response.status_code == 201
    created = create_response.json()
    conversation_id = UUID(created["id"])
    assert created["knowledge_base_id"] == knowledge_base_id
    assert created["title"] == "BGE-M3 notes"
    assert datetime.fromisoformat(created["created_at"]).tzinfo is not None
    assert datetime.fromisoformat(created["updated_at"]).tzinfo is not None

    detail_response = await client.get(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}"
    )

    assert detail_response.status_code == 200
    assert detail_response.json() == created


async def test_user_can_page_through_conversations_with_a_stable_cursor(
    client: AsyncClient,
) -> None:
    knowledge_base_id = await _create_knowledge_base(client)
    for title in ("First", "Second", "Third"):
        response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
            json={"title": title},
        )
        assert response.status_code == 201

    first_page_response = await client.get(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
        params={"limit": 2},
    )

    assert first_page_response.status_code == 200
    first_page = first_page_response.json()
    assert [item["title"] for item in first_page["items"]] == ["Third", "Second"]
    assert isinstance(first_page["next_cursor"], str)
    assert first_page["next_cursor"]

    second_page_response = await client.get(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
        params={"limit": 2, "cursor": first_page["next_cursor"]},
    )

    assert second_page_response.status_code == 200
    second_page = second_page_response.json()
    assert [item["title"] for item in second_page["items"]] == ["First"]
    assert second_page["next_cursor"] is None


async def test_conversation_cannot_be_read_through_another_knowledge_base(
    client: AsyncClient,
) -> None:
    owning_knowledge_base_id = await _create_knowledge_base(client, "Owner")
    other_knowledge_base_id = await _create_knowledge_base(client, "Other")
    create_response = await client.post(
        f"/api/v1/knowledge-bases/{owning_knowledge_base_id}/conversations",
        json={"title": "Private conversation"},
    )
    conversation_id = create_response.json()["id"]

    response = await client.get(
        f"/api/v1/knowledge-bases/{other_knowledge_base_id}/conversations/"
        f"{conversation_id}"
    )

    assert response.status_code == 404
    assert response.json()["code"] == "CONVERSATION_NOT_FOUND"


async def test_user_can_record_and_list_questions_in_a_conversation(
    client: AsyncClient,
) -> None:
    knowledge_base_id = await _create_knowledge_base(client)
    conversation_response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
        json={"title": "Embedding questions"},
    )
    conversation_id = conversation_response.json()["id"]

    create_response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
        f"{conversation_id}/questions",
        json={"content": "  How are vectors normalized?  "},
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert UUID(created["id"])
    assert created["conversation_id"] == conversation_id
    assert created["content"] == "How are vectors normalized?"
    assert datetime.fromisoformat(created["created_at"]).tzinfo is not None

    list_response = await client.get(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
        f"{conversation_id}/questions"
    )

    assert list_response.status_code == 200
    assert list_response.json() == {"items": [created], "next_cursor": None}


async def test_deleting_a_knowledge_base_removes_its_conversations_and_questions(
    client: AsyncClient,
) -> None:
    knowledge_base_id = await _create_knowledge_base(client)
    conversation_response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
        json={"title": "Disposable history"},
    )
    conversation_id = conversation_response.json()["id"]
    question_response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
        f"{conversation_id}/questions",
        json={"content": "Will this be removed?"},
    )
    assert question_response.status_code == 201

    delete_response = await client.delete(
        f"/api/v1/knowledge-bases/{knowledge_base_id}",
        params={"confirm": "true"},
    )

    assert delete_response.status_code == 204
    missing_response = await client.get(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}"
    )
    assert missing_response.status_code == 404
    assert missing_response.json()["code"] == "CONVERSATION_NOT_FOUND"


async def test_question_history_is_paginated_in_chronological_order(
    client: AsyncClient,
) -> None:
    knowledge_base_id = await _create_knowledge_base(client)
    conversation_response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
        json={"title": "Ordered history"},
    )
    conversation_id = conversation_response.json()["id"]
    for content in ("First question", "Second question", "Third question"):
        response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
            f"{conversation_id}/questions",
            json={"content": content},
        )
        assert response.status_code == 201

    first_page_response = await client.get(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
        f"{conversation_id}/questions",
        params={"limit": 2},
    )
    first_page = first_page_response.json()

    assert first_page_response.status_code == 200
    assert [item["content"] for item in first_page["items"]] == [
        "First question",
        "Second question",
    ]
    assert first_page["next_cursor"]

    second_page_response = await client.get(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
        f"{conversation_id}/questions",
        params={"limit": 2, "cursor": first_page["next_cursor"]},
    )

    assert second_page_response.status_code == 200
    assert [item["content"] for item in second_page_response.json()["items"]] == [
        "Third question"
    ]
    assert second_page_response.json()["next_cursor"] is None


async def test_questions_cannot_cross_the_conversation_knowledge_base_scope(
    client: AsyncClient,
) -> None:
    owning_knowledge_base_id = await _create_knowledge_base(client, "Owner")
    other_knowledge_base_id = await _create_knowledge_base(client, "Other")
    conversation_response = await client.post(
        f"/api/v1/knowledge-bases/{owning_knowledge_base_id}/conversations",
        json={"title": "Private history"},
    )
    conversation_id = conversation_response.json()["id"]

    create_response = await client.post(
        f"/api/v1/knowledge-bases/{other_knowledge_base_id}/conversations/"
        f"{conversation_id}/questions",
        json={"content": "Out-of-scope question"},
    )
    list_response = await client.get(
        f"/api/v1/knowledge-bases/{other_knowledge_base_id}/conversations/"
        f"{conversation_id}/questions"
    )

    assert create_response.status_code == 404
    assert create_response.json()["code"] == "CONVERSATION_NOT_FOUND"
    assert list_response.status_code == 404
    assert list_response.json()["code"] == "CONVERSATION_NOT_FOUND"


async def test_invalid_conversation_and_question_cursors_return_problem_details(
    client: AsyncClient,
) -> None:
    knowledge_base_id = await _create_knowledge_base(client)
    conversation_response = await client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
        json={"title": "Cursor checks"},
    )
    conversation_id = conversation_response.json()["id"]

    responses = []
    for cursor in ("%%%not-a-cursor%%%", "a"):
        responses.extend(
            [
                await client.get(
                    f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
                    params={"cursor": cursor},
                ),
                await client.get(
                    f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
                    f"{conversation_id}/questions",
                    params={"cursor": cursor},
                ),
            ]
        )

    for response in responses:
        assert response.status_code == 422
        assert response.json()["code"] == "INVALID_CURSOR"
        assert response.json()["request_id"]
