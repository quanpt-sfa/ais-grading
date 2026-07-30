import pytest
from decimal import Decimal
from src.db.sqlite_storage import SQLiteStorage
from src.student.manager import StudentInfo
from src.grading.engine import StudentGradeReport
from src.grading.base import CompareResult
from src.answer.models import MasterAnswerData, InventoryAnswer, FixedAssetAnswer, FixedAssetItem

def test_sqlite_storage_flow():
    storage = SQLiteStorage(db_path="data/test_history.db")
    
    st = StudentInfo(
        student_id="23729351",
        last_name="Phan Hữu",
        first_name="Thoại",
        class_name="DHKTKT18A",
        exam_room="B17",
        assigned_pc="B17M05",
        computer_name="DESKTOP-3FDJQC9"
    )
    
    score_data = {
        "final_score": 8.5,
        "weighted_total": 85.0,
        "scale": 10.0
    }
    
    module_results = {
        "transactions": CompareResult(
            module_id="transactions",
            display_name="Nghiệp vụ phát sinh",
            total_items=10,
            matched_items=10,
            match_ratio=1.0,
            score=55.0,
            max_weight=55.0
        )
    }
    
    report = StudentGradeReport(
        student=st,
        score_data=score_data,
        traceability_data={"purchase_chain": {"vouchers": 1}},
        module_results=module_results
    )
    
    exam_id = storage.save_grading_session([report], exam_name="Test Exam Run")
    assert exam_id > 0
    
    exams = storage.list_exams()
    assert len(exams) >= 1
    assert exams[0]["exam_name"] == "Test Exam Run"
    
    results = storage.get_exam_results(exam_id)
    assert len(results) == 1
    assert results[0]["student_id"] == "23729351"
    assert results[0]["final_score"] == 8.5

def test_sqlite_master_answer_caching():
    storage = SQLiteStorage(db_path="data/test_history.db")
    
    master = MasterAnswerData(
        inventory=InventoryAnswer(item_count=8, total_qty=Decimal('45180'), total_amount=Decimal('3043750000')),
        fixed_asset=FixedAssetAnswer(items=[
            FixedAssetItem("TS01", "Xe Tai", Decimal('63000000'), Decimal('63000000'), Decimal('21000000'), 36, 24)
        ])
    )
    
    ans_id = storage.save_master_answer(master, "HungBinh2024_Test")
    assert ans_id > 0
    
    loaded = storage.load_master_answer("HungBinh2024_Test")
    assert loaded is not None
    assert loaded.inventory.item_count == 8
    assert loaded.inventory.total_qty == Decimal('45180')
    assert len(loaded.fixed_asset.items) == 1
    assert loaded.fixed_asset.items[0].code == "TS01"
