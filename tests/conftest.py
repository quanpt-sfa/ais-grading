import os

import pytest


SQL_INTEGRATION_NODES = {
    "tests/test_connection.py::test_database_connection_list",
    "tests/test_connection.py::test_database_exists",
    "tests/test_grading.py::test_full_grading_pipeline",
    "tests/test_web.py::test_api_answer",
}


def pytest_collection_modifyitems(config, items):
    if os.getenv("RUN_SQL_INTEGRATION") == "1":
        return
    marker = pytest.mark.skip(
        reason=(
            "requires a Windows SQL Server/MISA database; "
            "set RUN_SQL_INTEGRATION=1 to execute"
        )
    )
    for item in items:
        if item.nodeid in SQL_INTEGRATION_NODES:
            item.add_marker(marker)
