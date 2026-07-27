from sourcetrace.main import create_app


def test_document_upload_and_listing_are_exposed_in_openapi() -> None:
    document = create_app().openapi()
    path = document["paths"]["/api/v1/knowledge-bases/{knowledge_base_id}/documents"]
    upload = path["post"]
    listing = path["get"]

    request_schema = upload["requestBody"]["content"]["multipart/form-data"]["schema"]
    request_body = document["components"]["schemas"][request_schema["$ref"].split("/")[-1]]
    assert request_body["required"] == ["file"]
    assert request_body["properties"]["file"]["contentMediaType"] == ("application/octet-stream")
    assert set(upload["responses"]) >= {"200", "202", "404", "413", "415", "422"}
    assert upload["responses"]["202"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/DocumentUploadResponse"
    )
    assert set(listing["responses"]) >= {"200", "404", "422"}
    parameters = {parameter["name"]: parameter for parameter in listing["parameters"]}
    assert parameters["limit"]["schema"]["default"] == 20
    assert parameters["cursor"]["required"] is False
