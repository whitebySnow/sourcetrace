from datetime import datetime
from uuid import UUID

from httpx import AsyncClient


async def test_user_can_create_and_retrieve_a_knowledge_base(client: AsyncClient) -> None:
    create_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "  Research Papers  "},
    )

    assert create_response.status_code == 201
    created = create_response.json()
    knowledge_base_id = UUID(created["id"])
    assert created["name"] == "Research Papers"
    assert datetime.fromisoformat(created["created_at"]).tzinfo is not None
    assert datetime.fromisoformat(created["updated_at"]).tzinfo is not None

    detail_response = await client.get(f"/api/v1/knowledge-bases/{knowledge_base_id}")

    assert detail_response.status_code == 200
    assert detail_response.json() == created


async def test_duplicate_knowledge_base_name_returns_a_conflict(client: AsyncClient) -> None:
    first_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Research Papers"},
    )

    duplicate_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "research papers"},
    )

    assert first_response.status_code == 201
    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["code"] == "KNOWLEDGE_BASE_NAME_CONFLICT"


async def test_user_can_page_through_knowledge_bases_with_an_opaque_cursor(
    client: AsyncClient,
) -> None:
    for name in ("First", "Second", "Third"):
        response = await client.post("/api/v1/knowledge-bases", json={"name": name})
        assert response.status_code == 201

    first_page_response = await client.get("/api/v1/knowledge-bases", params={"limit": 2})

    assert first_page_response.status_code == 200
    first_page = first_page_response.json()
    assert [item["name"] for item in first_page["items"]] == ["Third", "Second"]
    assert isinstance(first_page["next_cursor"], str)
    assert first_page["next_cursor"]

    second_page_response = await client.get(
        "/api/v1/knowledge-bases",
        params={"limit": 2, "cursor": first_page["next_cursor"]},
    )

    assert second_page_response.status_code == 200
    second_page = second_page_response.json()
    assert [item["name"] for item in second_page["items"]] == ["First"]
    assert second_page["next_cursor"] is None


async def test_permanent_delete_requires_confirmation(client: AsyncClient) -> None:
    create_response = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Delete Me"},
    )
    knowledge_base_id = create_response.json()["id"]

    unconfirmed_response = await client.delete(
        f"/api/v1/knowledge-bases/{knowledge_base_id}"
    )

    assert unconfirmed_response.status_code == 422
    assert (await client.get(f"/api/v1/knowledge-bases/{knowledge_base_id}")).status_code == 200

    delete_response = await client.delete(
        f"/api/v1/knowledge-bases/{knowledge_base_id}",
        params={"confirm": "true"},
    )

    assert delete_response.status_code == 204
    missing_response = await client.get(f"/api/v1/knowledge-bases/{knowledge_base_id}")
    assert missing_response.status_code == 404
    assert missing_response.json()["code"] == "KNOWLEDGE_BASE_NOT_FOUND"


async def test_invalid_pagination_cursor_returns_problem_details(client: AsyncClient) -> None:
    for cursor in ("%%%not-a-cursor%%%", "a"):
        response = await client.get(
            "/api/v1/knowledge-bases",
            params={"cursor": cursor},
        )

        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "INVALID_CURSOR"
        assert body["request_id"]


async def test_knowledge_base_name_is_validated_at_the_http_boundary(
    client: AsyncClient,
) -> None:
    for invalid_name in ("   ", "x" * 121):
        response = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": invalid_name},
        )

        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "VALIDATION_ERROR"
        assert body["errors"][0]["field"] == "name"
