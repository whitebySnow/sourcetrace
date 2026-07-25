from sourcetrace.main import create_app


def test_knowledge_base_lifecycle_is_exposed_in_openapi() -> None:
    document = create_app().openapi()
    collection = document["paths"]["/api/v1/knowledge-bases"]
    item = document["paths"]["/api/v1/knowledge-bases/{knowledge_base_id}"]

    assert set(collection) >= {"get", "post"}
    assert "200" in collection["get"]["responses"]
    assert "201" in collection["post"]["responses"]
    assert "409" in collection["post"]["responses"]
    assert "422" in collection["post"]["responses"]
    assert "200" in item["get"]["responses"]
    assert "404" in item["get"]["responses"]
    assert "204" in item["delete"]["responses"]
    assert "404" in item["delete"]["responses"]
    assert "422" in item["delete"]["responses"]

    error_schema = collection["post"]["responses"]["409"]["content"]["application/json"]["schema"]
    assert error_schema["$ref"] == "#/components/schemas/ErrorResponse"

    list_parameters = {
        parameter["name"]: parameter for parameter in collection["get"]["parameters"]
    }
    assert list_parameters["limit"]["schema"]["default"] == 20
    assert list_parameters["cursor"]["required"] is False

    delete_parameters = {parameter["name"]: parameter for parameter in item["delete"]["parameters"]}
    assert delete_parameters["confirm"]["required"] is True
    assert delete_parameters["confirm"]["schema"]["type"] == "boolean"
