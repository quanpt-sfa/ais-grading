import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
from decimal import Decimal
import logging

from src.answer.models import (
    MasterAnswerData, InventoryAnswer, FixedAssetAnswer, FixedAssetItem,
    GeneralBalanceAnswer, AccountBalanceItem, TransactionAnswer, TransactionItem,
    FinancialReportAnswer
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
            
            # 1. Bảng Kho Đáp Án Master (Master Answers Cache)
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

            # 2. Bảng Kỳ thi (Exams)
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

            # 3. Bảng Kết quả Sinh viên (Student Grade Results)
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
        logger.info(f"Đã khởi tạo SQLite Storage tại: {self.db_path}")

    def save_master_answer(self, master: MasterAnswerData, answer_name: str) -> int:
        """Lưu snapshot Bộ Đáp Án Master vào SQLite database."""
        inv_data = {
            "item_count": master.inventory.item_count if master.inventory else 0,
            "total_qty": str(master.inventory.total_qty) if master.inventory else "0",
            "total_amount": str(master.inventory.total_amount) if master.inventory else "0"
        }

        fa_data = [
            {
                "code": item.code,
                "name": item.name,
                "org_price": str(item.org_price),
                "depreciation_amount": str(item.depreciation_amount),
                "accum_depreciation_amount": str(item.accum_depreciation_amount),
                "lifetime_months": item.lifetime_months,
                "remaining_months": item.remaining_months
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
                "quantity": str(acc.quantity)
            }
            for code, acc in (master.general_balance.accounts.items() if master.general_balance else {}.items())
        }

        tx_data = [
            {
                "task_id": key[0],
                "account_code": key[1],
                "amount": str(item.amount),
                "amount_oc": str(item.amount_oc),
                "quantity": str(item.quantity)
            }
            for key, item in (master.transactions.entries.items() if master.transactions else {}.items())
        ]

        fr_data = [
            {
                "report_type": key[0],
                "item_code": key[1],
                "amount": str(amt)
            }
            for key, amt in (master.financial_reports.items.items() if master.financial_reports else {}.items())
        ]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
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
            """, (
                answer_name,
                json.dumps(inv_data, ensure_ascii=False),
                json.dumps(fa_data, ensure_ascii=False),
                json.dumps(gb_data, ensure_ascii=False),
                json.dumps(tx_data, ensure_ascii=False),
                json.dumps(fr_data, ensure_ascii=False)
            ))
            cursor.execute("SELECT id FROM master_answers WHERE answer_name = ?", (answer_name,))
            row = cursor.fetchone()
            ans_id = row["id"] if row else 1

        logger.info(f"Đã lưu thành công Đáp Án Master [{answer_name}] (ID: {ans_id}) vào SQLite database.")
        return ans_id

    def load_master_answer(self, answer_name: str) -> Optional[MasterAnswerData]:
        """Tải Đáp Án Master từ SQLite database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM master_answers WHERE answer_name = ?", (answer_name,))
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
                total_amount=Decimal(str(inv_raw.get("total_amount", "0")))
            )

            fa_items = [
                FixedAssetItem(
                    code=item["code"],
                    name=item.get("name", ""),
                    org_price=Decimal(item["org_price"]),
                    depreciation_amount=Decimal(item["depreciation_amount"]),
                    accum_depreciation_amount=Decimal(item["accum_depreciation_amount"]),
                    lifetime_months=int(item["lifetime_months"]),
                    remaining_months=int(item["remaining_months"])
                )
                for item in fa_raw
            ]
            master.fixed_asset = FixedAssetAnswer(items=fa_items)

            gb_accs = {
                code: AccountBalanceItem(
                    account_code=code,
                    debit_amount=Decimal(acc["debit_amount"]),
                    debit_amount_oc=Decimal(acc["debit_amount_oc"]),
                    credit_amount=Decimal(acc["credit_amount"]),
                    credit_amount_oc=Decimal(acc["credit_amount_oc"]),
                    quantity=Decimal(acc["quantity"])
                )
                for code, acc in gb_raw.items()
            }
            master.general_balance = GeneralBalanceAnswer(accounts=gb_accs)

            tx_entries = {
                (int(t["task_id"]), str(t["account_code"])): TransactionItem(
                    task_id=int(t["task_id"]),
                    account_code=str(t["account_code"]),
                    amount=Decimal(t["amount"]),
                    amount_oc=Decimal(t["amount_oc"]),
                    quantity=Decimal(t["quantity"])
                )
                for t in tx_raw
            }
            master.transactions = TransactionAnswer(entries=tx_entries)

            fr_items = {
                (str(f["report_type"]), str(f["item_code"])): Decimal(f["amount"])
                for f in fr_raw
            }
            master.financial_reports = FinancialReportAnswer(items=fr_items)

            return master

    def list_saved_master_answers(self) -> List[Dict[str, Any]]:
        """Danh sách các bộ đáp án Master đã được lưu trong SQLite."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, answer_name, created_at FROM master_answers ORDER BY created_at DESC")
            return [dict(r) for r in cursor.fetchall()]

    def save_grading_session(self, reports: List[Any], exam_name: str = None, config: dict = None) -> int:
        """Lưu toàn bộ đợt chấm điểm vào SQLite."""
        if not reports:
            return 0

        if not exam_name:
            exam_name = f"Đợt chấm thi {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"

        scores = [r.score_data.get("final_score", 0.0) for r in reports]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        max_score = max(scores) if scores else 0.0
        pass_count = sum(1 for s in scores if s >= 5.0)
        pass_rate = (pass_count / len(scores)) * 100 if scores else 0.0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO exams (exam_name, student_count, avg_score, max_score, pass_rate, config_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                exam_name,
                len(reports),
                round(avg_score, 2),
                round(max_score, 2),
                round(pass_rate, 1),
                json.dumps(config or {}, ensure_ascii=False)
            ))
            
            exam_id = cursor.lastrowid

            for r in reports:
                st = r.student
                mod_summary = {}
                for mid, res in r.module_results.items():
                    mod_summary[mid] = {
                        "display_name": res.display_name,
                        "score": res.score,
                        "max_weight": res.max_weight,
                        "match_ratio": round(res.match_ratio * 100, 1),
                        "matched_items": res.matched_items,
                        "total_items": res.total_items,
                        "details": res.details
                    }

                cursor.execute("""
                    INSERT INTO student_results (
                        exam_id, student_id, full_name, class_name, exam_room,
                        assigned_pc, actual_pc, has_pc_mismatch, final_score,
                        weighted_total, scale, modules_json, traceability_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    exam_id,
                    st.student_id,
                    st.full_name,
                    st.class_name,
                    st.exam_room,
                    st.assigned_pc,
                    st.computer_name,
                    1 if st.has_pc_mismatch else 0,
                    r.score_data.get("final_score", 0.0),
                    r.score_data.get("weighted_total", 0.0),
                    r.score_data.get("scale", 10.0),
                    json.dumps(mod_summary, default=str, ensure_ascii=False),
                    json.dumps(r.traceability_data or {}, default=str, ensure_ascii=False)
                ))

            conn.commit()

        logger.info(f"Đã lưu thành công kỳ thi [ID: {exam_id}] với {len(reports)} sinh viên vào SQLite.")
        return exam_id

    def list_exams(self) -> List[Dict[str, Any]]:
        """Danh sách tất cả các đợt chấm thi đã lưu trong SQLite."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM exams ORDER BY created_at DESC")
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_exam_results(self, exam_id: int) -> List[Dict[str, Any]]:
        """Lấy danh sách kết quả chi tiết của 1 đợt thi theo exam_id."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM student_results WHERE exam_id = ? ORDER BY student_id", (exam_id,))
            rows = cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                item["modules"] = json.loads(item.get("modules_json") or "{}")
                item["traceability"] = json.loads(item.get("traceability_json") or "{}")
                results.append(item)
            return results
