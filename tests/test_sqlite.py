from decimal import Decimal

from src.db.sqlite_storage import SQLiteStorage
from src.student.manager import StudentInfo
from src.grading.engine import StudentGradeReport
from src.grading.base import CompareResult
from src.answer.models import (
    MasterAnswerData,
    InventoryAnswer,
    FixedAssetAnswer,
    FixedAssetItem,
    FinancialReportAnswer,
    FinancialReportLine,
)


def test_sqlite_storage_flow(tmp_path):
    storage = SQLiteStorage(db_path=str(tmp_path / "history.db"))

    student = StudentInfo(
        student_id="23729351",
        last_name="Phan Hữu",
        first_name="Thoại",
        class_name="DHKTKT18A",
        exam_room="B17",
        assigned_pc="B17M05",
        computer_name="DESKTOP-3FDJQC9",
    )

    score_data = {
        "final_score": 8.5,
        "weighted_total": 85.0,
        "scale": 10.0,
    }

    module_results = {
        "transactions": CompareResult(
            module_id="transactions",
            display_name="Nghiệp vụ phát sinh",
            total_items=10,
            matched_items=10,
            match_ratio=1.0,
            score=55.0,
            max_weight=55.0,
        )
    }

    report = StudentGradeReport(
        student=student,
        score_data=score_data,
        traceability_data={"purchase_chain": {"vouchers": 1}},
        module_results=module_results,
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


def report_line(detail_id: str, amount: str) -> FinancialReportLine:
    return FinancialReportLine(
        report_detail_id=detail_id,
        report_ref_id="report-1",
        report_type="1",
        item_id=None,
        item_code="100",
        item_index=1,
        sort_order=1,
        category=0,
        formula_type=0,
        amount=Decimal(amount),
        report_ref_type=100,
        period=12,
        year=2024,
        from_date="2024-01-01",
        to_date="2024-12-31",
        currency_id="VND",
    )


def test_sqlite_master_answer_caching_preserves_duplicate_report_lines(tmp_path):
    storage = SQLiteStorage(db_path=str(tmp_path / "history.db"))

    master = MasterAnswerData(
        inventory=InventoryAnswer(
            item_count=8,
            total_qty=Decimal("45180"),
            total_amount=Decimal("3043750000"),
        ),
        fixed_asset=FixedAssetAnswer(
            items=[
                FixedAssetItem(
                    "TS01",
                    "Xe Tai",
                    Decimal("63000000"),
                    Decimal("63000000"),
                    Decimal("21000000"),
                    36,
                    24,
                )
            ]
        ),
        financial_reports=FinancialReportAnswer(
            lines=[
                report_line("detail-1", "10"),
                report_line("detail-2", "20"),
            ]
        ),
    )

    answer_id = storage.save_master_answer(master, "HungBinh2024_Test")
    assert answer_id > 0

    loaded = storage.load_master_answer("HungBinh2024_Test")
    assert loaded is not None
    assert loaded.inventory.item_count == 8
    assert loaded.inventory.total_qty == Decimal("45180")
    assert len(loaded.fixed_asset.items) == 1
    assert loaded.fixed_asset.items[0].code == "TS01"
    assert len(loaded.financial_reports.lines) == 2
    assert [line.report_detail_id for line in loaded.financial_reports.lines] == [
        "detail-1",
        "detail-2",
    ]
    assert [line.amount for line in loaded.financial_reports.lines] == [
        Decimal("10"),
        Decimal("20"),
    ]
