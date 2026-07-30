import pytest
from fastapi.testclient import TestClient
from src.web.app import app

client = TestClient(app)

def test_index_page():
    response = client.get("/")
    assert response.status_code == 200
    assert "Hệ Thống Chấm Điểm HTTTKT" in response.text

def test_api_config():
    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "scoring" in data

def test_api_answer():
    response = client.get("/api/answer")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "inventory" in data
