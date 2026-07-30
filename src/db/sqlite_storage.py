import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
from decimal import Decimal
import logging

from src.answer.models import (
    MasterAnswerData,
    InventoryAnswer,
    FixedAssetAnswer,
    FixedAssetItem,
    GeneralBalanceAnswer,
    AccountBalanceItem,
    TransactionAnswer,
    TransactionItem,
    FinancialReportAnswer,
    FinancialReportLine,
)

logger = logging.getLogger(__name__)


class SQLiteStorage:
    """Quản lý lưu trữ lịch sử chấm điểm và kho Đáp Án Master dài hạn vào SQLite."""

    def __init__(self, db_path: str = "data/grading_history.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Khởi tạo bảng SQLite lưu trữ đáp án master, lịch sử bài thi và điểm số."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS master_answers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    answer_name TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    inventory_json TEXT,
                    fixed_asset_json TEXT,
                    general_balance_json TEXT,
                    transactions_json TEXT,
                    financial_reports_json TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS exams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exam_name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    student_count INTEGER DEFAULT 0,
                    avg_score REAL DEFAULT 0.0,
                    max_score REAL DEFAULT 0.0,
                    pass_rate REAL DEFAULT 0.0,
                    config_json TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS student_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exam_id INTEGER,
                    student_id TEXT NOT NULL,
                    full_name TEXT,
                    class_name TEXT,
                    exam_room TEXT,
                    assigned_pc TEXT,
                    actual_pc TEXT,
                    has_pc_mismatch INTEGER DEFAULT 0,
                    final_score REAL DEFAULT 0.0,
                    weighted_total REAL DEFAULT 0.0,
                    scale REAL DEFAULT 10.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    modules_json TEXT,
                    traceability_json TEXT,
                    FOREIGN KEY(exam_id) REFERENCES exams(id) ON DELETE CASCADE
                )
            """)

            conn.commit()
        logger.info("Đã khởi tạo SQLite Storage tại: %s", self.db_path)

    @staticmethod
    def _json_scalar(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (datetime,)):
            return value.isoformat()
        return str(value) if not isinstance(value, (str, int, float, bool)) else value

    @classmethod
    def _serialize_report_line(cls, line: FinancialReportLine) -> Dict[str, Any]:
        return {
            "report_detail_id": line.report_detail_id,
            "report_ref_id": line.report_ref_id,
            "report_type": line.report_type,
            "item_id": line.item_id,
            "item_code": line.item_code,
            "item_index": line.item_index,
            "sort_order": line.sort_order,
            "category": line.category,
            "formula_type": line.formula_type,
            "amount": str(line.amount),
            "prev_amount": str(line.prev_amount),
            "other_amount": str(line.other_amount),
            "other_prev_amount": str(line.other_prev_amount),
            "report_ref_type": line.report_ref_type,
            "display_on_book": line.display_on_book,
            "branch_id": line.branch_id,
            "period": line.period,
            "year": line.year,
            "period_name": line.period_name,
            "report_name": line.report_name,
            "from_date": cls._json_scalar(line.from_date),
            "to_date": cls._json_scalar(line.to_date),
            "currency_id": line.currency_id,
            "is_report_finance_audit": line.is_report_finance_audit,
        }

    @staticmethod
    def _deserialize_report_line(item: Dict[str, Any]) -> FinancialReportLine:
        # Tương thích cache cũ chỉ có report_type, item_code và amount.
        return FinancialReportLine(
            report_detail_id=item.get("report_detail_id"),
            report_ref_id=item.get("report_ref_id"),
            report_type=str(item.get("report_type", "")),
            item_id=item.get("item_id"),
            item_code=str(item.get("item_code", "")),
            item_index=(
                int(item["item_index"])
                if item.get("item_index") is not None
                else None
            ),
            sort_order=(
                int(item["sort_order"])
                if item.get("sort_order") is not None
                else None
            ),
            category=(
                int(item["category"])
                if item.get("category") is not None
                else None
            ),
            formula_type=(
                int(item["formula_type"])
                if item.get("formula_type") is not None
                else None
            ),
            amount=Decimal(str(item.get("amount", "0"))),
            prev_amount=Decimal(str(item.get("prev_amount", "0"))),
            other_amount=Decimal(str(item.get("other_amount", "0"))),
            other_prev_amount=Decimal(
                str(item.get("other_prev_amount", "0"))
            ),
            report_ref_type=(
                int(item["report_ref_type"])
                if item.get("report_ref_type") is not None
                else None
            ),
            display_on_book=(
                int(item["display_on_book"])
                if item.get("display_on_book") is not None
                else None
            ),
            branch_id=item.get("branch_id"),
            period=(
                int(item["period"])
                if item.get("period") is not None
                else None
            ),
            year=(
                int(item["year"])
                if item.get("year") is not None
                else None
            ),
            period_name=item.get("period_name"),
            report_name=item.get("report_name"),
            from_date=item.get("from_date"),
            to_date=item.get("to_date"),
            currency_id=item.get("currency_id"),
            is_report_finance_audit=item.get("is_report_finance_audit"),
        )

    def save_master_answer(self, master: MasterAnswerData, answer_name: str) -> int:
        """Lưu snapshot Bộ Đáp Án Master vào SQLite database."""
        inv_data = {
            "item_count": master.inventory.item_count if master.inventory else 0,
            "total_qty": str(master.inventory.total_qty) if master.inventory else "0",
            "total_amount": str(master.inventory.total_amount) if master.inventory else "0",
        }

        fa_data = [
            {
                "code": item.code,
                "name": item.name,
                "org_price": str(item.org_price),
                "depreciation_amount": str(item.depreciation_amount),
                "accum_depreciation_amount": str(item.accum_depreciation_amount),
                "lifetime_months": item.lifetime_months,
                "remaining_months": item.remaining_months,
            }
            for item in (master.fixed_asset.items if master.fixed_asset else [])
        ]

        gb_data = {
            code: {
                "account_code": acc.account_code,
                "debit_amount": str(acc.debit_amount),
                "debit_amount_oc": str(acc.debit_amount_oc),
                "credit_amount": str(acc.credit_amount),
                "credit_amount_oc": str(acc.credit_amount_oc),
                "quantity": str(acc.quantity),
            }
            for code, acc in (
                master.general_balance.accounts.items()
                if master.general_balance
                else {}.items()
            )
        }

        tx_data = [
            {
                "task_id": key[0],
                "account_code": key[1],
                "amount": str(item.amount),
                "amount_oc": str(item.amount_oc),
                "quantity": str(item.quantity),
            }
            for key, item in (
                master.transactions.entries.items()
                if master.transactions
                else {}.items()
            )
        ]

        fr_data = [
            self._serialize_report_line(line)
            for line in (
                master.financial_reports.lines
                if master.financial_reports
                else []
            )
        ]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO master_answers (
                    answer_name, inventory_json, fixed_asset_json,
                    general_balance_json, transactions_json, financial_reports_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(answer_name) DO UPDATE SET
                    created_at = CURRENT_TIMESTAMP,
                    inventory_json = excluded.inventory_json,
                    fixed_asset_json = excluded.fixed_asset_json,
                    general_balance_json = excluded.general_balance_json,
                    transactions_json = excluded.transactions_json,
                    financial_reports_json = excluded.financial_reports_json
                """,
                (
                    answer_name,
                    json.dumps(inv_data, ensure_ascii=False),
                    json.dumps(fa_data, ensure_ascii=False),
                    json.dumps(gb_data, ensure_ascii=False),
                    json.dumps(tx_data, ensure_ascii=False),
                    json.dumps(fr_data, ensure_ascii=False),
                ),
            )
            cursor.execute(
                "SELECT id FROM master_answers WHERE answer_name = ?",
                (answer_name,),
            )
            row = cursor.fetchone()
            ans_id = row["id"] if row else 1

        logger.info(
            "Đã lưu thành công Đáp Án Master [%s] (ID: %s) vào SQLite database.",
            answer_name,
            ans_id,
        )
        return ans_id

    def load_master_answer(self, answer_name: str) -> Optional[MasterAnswerData]:
        """Tải Đáp Án Master từ SQLite database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM master_answers WHERE answer_name = ?",
                (answer_name,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            inv_raw = json.loads(row["inventory_json"] or "{}")
            fa_raw = json.loads(row["fixed_asset_json"] or "[]")
            gb_raw = json.loads(row["general_balance_json"] or "{}")
            tx_raw = json.loads(row["transactions_json"] or "[]")
            fr_raw = json.loads(row["financial_reports_json"] or "[]")

            master = MasterAnswerData()
            master.inventory = InventoryAnswer(
                item_count=int(inv_raw.get("item_count", 0)),
                total_qty=Decimal(str(inv_raw.get("total_qty", "0"))),
                total_amount=Decimal(str(inv_raw.get("total_amount", "0"))),
            )

            master.fixed_asset = FixedAssetAnswer(
                items=[
                    FixedAssetItem(
                        code=item["code"],
                        name=item.get("name", ""),
                        org_price=Decimal(item["org_price"]),
                        depreciation_amount=Decimal(item["depreciation_amount"]),
                        accum_depreciation_amount=Decimal(
                            item["accum_depreciation_amount"]
                        ),
                        lifetime_months=int(item["lifetime_months"]),
                        remaining_months=int(item["remaining_months"]),
                    )
                    for item in fa_raw
                ]
            )

            master.general_balance = GeneralBalanceAnswer(
                accounts={
                    code: AccountBalanceItem(
                        account_code=code,
                        debit_amount=Decimal(acc["debit_amount"]),
                        debit_amount_oc=Decimal(acc["debit_amount_oc"]),
                        credit_amount=Decimal(acc["credit_amount"]),
                        credit_amount_oc=Decimal(acc["credit_amount_oc"]),
                        quantity=Decimal(acc["quantity"]),
                    )
                    for code, acc in gb_raw.items()
                }
            )

            master.transactions = TransactionAnswer(
                entries={
                    (int(item["task_id"]), str(item["account_code"])): TransactionItem(
                        task_id=int(item["task_id"]),
                        account_code=str(item["account_code"]),
                        amount=Decimal(item["amount"]),
                        amount_oc=Decimal(item["amount_oc"]),
                        quantity=Decimal(item["quantity"]),
                    )
                    for item in tx_raw
                }
            )

            master.financial_reports = FinancialReportAnswer(
                lines=[self._deserialize_report_line(item) for item in fr_raw]
            )
            return master

    def list_saved_master_answers(self) -> List[Dict[str, Any]]:
        """Danh sách các bộ đáp án Master đã được lưu trong SQLite."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, answer_name, created_at "
                "FROM master_answers ORDER BY created_at DESC"
            )
            return [dict(row) for row in cursor.fetchall()]

    def save_grading_session(
        self,
        reports: List[Any],
        exam_name: str = None,
        config: dict = None,
    ) -> int:
        """Lưu toàn bộ đợt chấm điểm vào SQLite."""
        if not reports:
            return 0

        if not exam_name:
            exam_name = (
                f"Đợt chấm thi {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
            )

        scores = [report.score_data.get("final_score", 0.0) for report in reports]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        max_score = max(scores) if scores else 0.0
        pass_count = sum(1 for score in scores if score >= 5.0)
        pass_rate = (pass_count / len(scores)) * 100 if scores else 0.0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO exams (
                    exam_name, student_count, avg_score, max_score,
                    pass_rate, config_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    exam_name,
                    len(reports),
                    round(avg_score, 2),
                    round(max_score, 2),
                    round(pass_rate, 1),
                    json.dumps(config or {}, ensure_ascii=False),
                ),
            )
            exam_id = cursor.lastrowid

            for report in reports:
                student = report.student
                module_summary = {}
                for module_id, result in report.module_results.items():
                    module_summary[module_id] = {
                        "display_name": result.display_name,
                        "score": result.score,
                        "max_weight": result.max_weight,
                        "match_ratio": round(result.match_ratio * 100, 1),
                        "matched_items": result.matched_items,
                        "total_items": result.total_items,
                        "details": result.details,
                    }

                cursor.execute(
                    """
                    INSERT INTO student_results (
                        exam_id, student_id, full_name, class_name, exam_room,
                        assigned_pc, actual_pc, has_pc_mismatch, final_score,
                        weighted_total, scale, modules_json, traceability_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        exam_id,
                        student.student_id,
                        student.full_name,
                        student.class_name,
                        student.exam_room,
                        student.assigned_pc,
                        student.computer_name,
                        1 if student.has_pc_mismatch else 0,
                        report.score_data.get("final_score", 0.0),
                        report.score_data.get("weighted_total", 0.0),
                        report.score_data.get("scale", 10.0),
                        json.dumps(
                            module_summary,
                            default=str,
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            report.traceability_data or {},
                            default=str,
                            ensure_ascii=False,
                        ),
                    ),
                )
            conn.commit()

        logger.info(
            "Đã lưu thành công kỳ thi [ID: %s] với %s sinh viên vào SQLite.",
            exam_id,
            len(reports),
        )
        return exam_id

    def list_exams(self) -> List[Dict[str, Any]]:
        """Danh sách tất cả các đợt thi đã lưu."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM exams ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def get_exam_results(self, exam_id: int) -> List[Dict[str, Any]]:
        """Lấy danh sách kết quả chi tiết của một đợt thi."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM student_results "
                "WHERE exam_id = ? ORDER BY student_id",
                (exam_id,),
            )
            results = []
            for row in cursor.fetchall():
                item = dict(row)
                item["modules"] = json.loads(item.get("modules_json") or "{}")
                item["traceability"] = json.loads(
                    item.get("traceability_json") or "{}"
                )
                results.append(item)
            return results
