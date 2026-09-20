from fastapi.testclient import TestClient

from apps.api.main import app


def test_liveness() -> None:
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_meta_exposes_execution_limits() -> None:
    response = TestClient(app).get("/api/v1/meta")
    assert response.status_code == 200
    assert response.json()["limits"] == {
        "max_graph_steps": 30,
        "max_tool_calls": 5,
        "max_llm_calls": 3,
    }
