from __future__ import annotations

from fastapi.testclient import TestClient

from neo.rest.app import create_app


def test_rest_health_endpoint():
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "db_scheme" in body
    assert "agent_name" in body


def test_rest_node_round_trip():
    app = create_app()
    with TestClient(app) as client:
        create_response = client.post(
            "/api/nodes",
            json={
                "node_type": "concept",
                "title": "Semantic Memory",
                "content": "Structured knowledge",
                "domain": "memory",
            },
        )
        list_response = client.get("/api/nodes", params={"domain": "memory"})
        node_id = create_response.json()["id"]
        read_response = client.get(f"/api/nodes/{node_id}")
        branch_response = client.get(f"/api/nodes/{node_id}/branch")
        title_lookup_response = client.get("/api/nodes/by-title", params={"title": "Semantic Memory"})

    assert create_response.status_code == 200
    assert list_response.status_code == 200
    assert read_response.status_code == 200
    assert branch_response.status_code == 200
    assert title_lookup_response.status_code == 200
    assert any(node["title"] == "Semantic Memory" for node in list_response.json())
    assert read_response.json()["node"]["title"] == "Semantic Memory"
    assert branch_response.json()["root"]["title"] == "Semantic Memory"
    assert title_lookup_response.json()["selected_match"]["title"] == "Semantic Memory"


def test_rest_title_lookup_surfaces_ambiguity():
    app = create_app()
    title = "Agents Ambiguity Test"
    with TestClient(app) as client:
        client.post(
            "/api/nodes",
            json={
                "node_type": "concept",
                "title": title,
                "content": "Agent ontology root",
                "domain": "agents",
                "confidence": 0.95,
            },
        )
        client.post(
            "/api/nodes",
            json={
                "node_type": "concept",
                "title": title,
                "content": "Secondary branch",
                "domain": "operations",
                "confidence": 0.7,
            },
        )
        response = client.get("/api/nodes/by-title", params={"title": title})

    assert response.status_code == 200
    body = response.json()
    assert body["ambiguous"] is True
    assert body["count"] == 2
    assert body["selected_match"]["domain"] == "agents"


def test_rest_validation_errors():
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/nodes",
            json={"node_type": "concept", "title": "", "content": ""},
        )

    assert response.status_code == 422
