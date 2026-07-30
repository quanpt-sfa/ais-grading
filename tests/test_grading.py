import pytest
import yaml
from src.db.connection import DatabaseConnection
from src.db.queries import QueryRepository
from src.answer.loader import AnswerLoader
from src.grading.engine import GradingEngine
from src.student.manager import StudentInfo

def test_full_grading_pipeline():
    with open("config/grading_config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    db_conn = DatabaseConnection(host=".\\MC22", auth="windows")
    query_repo = QueryRepository("config/queries.yaml")

    engine = GradingEngine(cfg, db_conn, query_repo)
    engine.initialize()

    assert engine.master_answer is not None
    assert engine.master_answer.inventory is not None

    test_student = StudentInfo(student_id="HungBinh2024", last_name="Test", first_name="User", db_name="HungBinh2024")
    report = engine.grade_student(test_student)

    assert report is not None
    assert report.score_data["final_score"] >= 0.0
    assert "transactions" in report.module_results
    assert report.module_results["transactions"].match_ratio == 1.0
