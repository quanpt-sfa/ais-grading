import pytest
from fastapi.testclient import TestClient
from src.web.app import app

client = TestClient(app)


def test_index_page():
    response = client.get("/")
    assert response.status_code == 200
    assert "Hệ Thống Chấm Điểm HTTTKT" in response.text


def test_answer_restore_page():
    response = client.get("/answer/restore")
    assert response.status_code == 200
    assert ".bak" in response.text
    assert ".mbk" in response.text
    assert "/api/answer/restore" in response.text


def test_api_config():
    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "scoring" in data


@pytest.mark.integration
@pytest.mark.skipif(
    __import__("os").environ.get("RUN_SQL_INTEGRATION") != "1",
    reason="requires Windows SQL Server/MISA",
)
def test_api_answer():
    response = client.get("/api/answer")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "inventory" in data
