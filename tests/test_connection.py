import pytest
from src.db.connection import DatabaseConnection

def test_database_connection_list():
    conn = DatabaseConnection(host=".\\MC22", auth="windows")
    dbs = conn.list_databases()
    assert isinstance(dbs, list)
    assert "HungBinh2024" in dbs or "master" in dbs

def test_database_exists():
    conn = DatabaseConnection(host=".\\MC22", auth="windows")
    assert conn.database_exists("master") is True
    assert conn.database_exists("NonExistentDatabase999") is False
