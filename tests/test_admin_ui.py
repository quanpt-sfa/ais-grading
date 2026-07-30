from fastapi.testclient import TestClient

from src.web import app
from src.web.admin_routes import ADMIN_PAGE, _enhance_index_html
from src.web.app import INDEX_HTML


client = TestClient(app)


def test_admin_console_exposes_backend_operations():
    response = client.get("/admin")

    assert response.status_code == 200
    assert 'name="backup_set_position"' in response.text
    assert 'id="server-username"' in response.text
    assert 'id="sources-body"' in response.text
    assert 'id="history-body"' in response.text
    assert 'id="students-body"' in response.text


def test_restore_route_uses_complete_admin_console():
    response = client.get("/answer/restore")

    assert response.status_code == 200
    assert response.text == ADMIN_PAGE
    assert "Backup set position" in response.text


def test_main_ui_links_admin_and_removes_excel_ground_truth_controls():
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/admin"' in response.text
    assert "File Excel Đáp Án (Fallback)" not in response.text
    assert "Phục hồi BAK/MBK, xem snapshot và audit" in response.text
    assert "gradeSingleStudent" in response.text
    assert "Chấm riêng sinh viên này" in response.text


def test_admin_config_get_does_not_expose_password():
    response = client.get("/api/admin/config")

    assert response.status_code == 200
    data = response.json()
    assert "password" not in data["server"]
    assert "has_password" in data["server"]
    assert "answer_restore" in data


def test_index_transform_is_idempotent_for_required_features():
    enhanced = _enhance_index_html(INDEX_HTML)

    assert enhanced.count('href="/admin"') >= 1
    assert "File Excel Đáp Án (Fallback)" not in enhanced
    assert "gradeSingleStudent" in enhanced
