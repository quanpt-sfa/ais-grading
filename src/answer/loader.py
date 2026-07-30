import openpyxl
from decimal import Decimal
from typing import Dict, Any, Optional
import logging
from pathlib import Path

from src.db.connection import DatabaseConnection
from src.db.queries import QueryRepository
from src.answer.models import (
    MasterAnswerData, InventoryAnswer, FixedAssetAnswer, FixedAssetItem,
    GeneralBalanceAnswer, AccountBalanceItem, TransactionAnswer, TransactionItem,
    FinancialReportAnswer
)

logger = logging.getLogger(__name__)

class AnswerLoader:
    """Load đáp án từ SQL Server hoặc Excel fallback."""

    def __init__(self, db_conn: DatabaseConnection, query_repo: QueryRepository, config: Dict[str, Any]):
        self.db_conn = db_conn
        self.query_repo = query_repo
        self.config = config
        self.answer_config = config.get("answer", {})
        self.answer_db = self.answer_config.get("database", "HungBinh2024")
        self.excel_path = self.answer_config.get("excel_fallback", "d12.xlsm")
        self.excel_sheet = self.answer_config.get("excel_sheet_answer", "DapAn")

    def load(self) -> MasterAnswerData:
        """Load đầy đủ đáp án từ Database + Excel fallback nếu DB thiếu data."""
        logger.info(f"Đang load đáp án từ database [{self.answer_db}]...")
        
        master = MasterAnswerData()
        
        # 1. Try DB loading
        try:
            db_inv = self.query_repo.execute(self.db_conn, self.answer_db, "inventory_balance")
            if db_inv and db_inv[0]["item_count"] > 0:
                master.inventory = InventoryAnswer(
                    item_count=int(db_inv[0]["item_count"]),
                    total_qty=Decimal(str(db_inv[0]["total_qty"])),
                    total_amount=Decimal(str(db_inv[0]["total_amount"]))
                )
            
            db_fa = self.query_repo.execute(self.db_conn, self.answer_db, "fixed_asset_balance")
            if db_fa:
                fa_items = []
                for row in db_fa:
                    fa_items.append(FixedAssetItem(
                        code=str(row.get("FixedAssetCode", "")),
                        name=str(row.get("FixedAssetName", "")),
                        org_price=Decimal(str(row.get("OrgPrice", 0))),
                        depreciation_amount=Decimal(str(row.get("DepreciationAmount", 0))),
                        accum_depreciation_amount=Decimal(str(row.get("AccumDepreciationAmount", 0))),
                        lifetime_months=int(row.get("LifeTimeInMonth", 0) or 0),
                        remaining_months=int(row.get("LifeTimeRemainingInMonth", 0) or 0)
                    ))
                master.fixed_asset = FixedAssetAnswer(items=fa_items)

            db_gb = self.query_repo.execute(self.db_conn, self.answer_db, "general_balance")
            if db_gb:
                gb_accounts = {}
                for row in db_gb:
                    code = str(row["AccountCode"])
                    gb_accounts[code] = AccountBalanceItem(
                        account_code=code,
                        debit_amount=Decimal(str(row.get("DebitAmount", 0))),
                        debit_amount_oc=Decimal(str(row.get("DebitAmountOC", 0))),
                        credit_amount=Decimal(str(row.get("CreditAmount", 0))),
                        credit_amount_oc=Decimal(str(row.get("CreditAmountOC", 0))),
                        quantity=Decimal(str(row.get("Quantity", 0)))
                    )
                master.general_balance = GeneralBalanceAnswer(accounts=gb_accounts)

            db_tx = self.query_repo.execute(self.db_conn, self.answer_db, "transactions")
            if db_tx:
                tx_entries = {}
                for row in db_tx:
                    task_id = int(row["TaskID"])
                    code = str(row["AccountCode"])
                    tx_entries[(task_id, code)] = TransactionItem(
                        task_id=task_id,
                        account_code=code,
                        amount=Decimal(str(row.get("Amount", 0))),
                        amount_oc=Decimal(str(row.get("AmountOC", 0))),
                        quantity=Decimal(str(row.get("Quantity", 0)))
                    )
                master.transactions = TransactionAnswer(entries=tx_entries)

            db_fr = self.query_repo.execute(self.db_conn, self.answer_db, "financial_reports")
            if db_fr:
                fr_items = {}
                for row in db_fr:
                    rtype = str(row["ReportType"])
                    code = str(row["ItemCode"])
                    fr_items[(rtype, code)] = Decimal(str(row.get("Amount", 0)))
                master.financial_reports = FinancialReportAnswer(items=fr_items)

        except Exception as e:
            logger.warning(f"Không thể load đáp án trực tiếp từ DB [{self.answer_db}]: {e}")

        # 2. Check fallback to Excel for missing components
        if Path(self.excel_path).exists():
            logger.info(f"Bổ sung đáp án từ file Excel: {self.excel_path} (sheet '{self.excel_sheet}')")
            self._fill_from_excel(master)

        # 3. Save snapshot to SQLite Storage for long-term archiving & offline reuse
        try:
            from src.db.sqlite_storage import SQLiteStorage
            storage = SQLiteStorage()
            if master.transactions or master.inventory:
                storage.save_master_answer(master, self.answer_db)
        except Exception as e:
            logger.debug(f"Không thể lưu master answer cache vào SQLite: {e}")
            
        return master

    def _fill_from_excel(self, master: MasterAnswerData):
        """Đọc sheet DapAn từ Excel nếu DB thiếu thông tin số dư đầu kỳ hoặc BCTC."""
        wb = openpyxl.load_workbook(self.excel_path, read_only=True, data_only=True)
        if self.excel_sheet not in wb.sheetnames:
            wb.close()
            return
            
        ws = wb[self.excel_sheet]
        
        # Parse Inventory balance if missing
        if not master.inventory or master.inventory.item_count == 0:
            a8 = ws['A8'].value
            b8 = ws['B8'].value
            c8 = ws['C8'].value
            if a8 is not None and b8 is not None and c8 is not None:
                master.inventory = InventoryAnswer(
                    item_count=int(a8),
                    total_qty=Decimal(str(b8)),
                    total_amount=Decimal(str(c8))
                )

        # Parse Fixed Asset if missing
        if not master.fixed_asset or not master.fixed_asset.items:
            fa_items = []
            for r in range(8, 15):
                d = ws[f'D{r}'].value
                if d is not None and isinstance(d, (int, float)):
                    fa_items.append(FixedAssetItem(
                        code=f"TS00{r-7}",
                        name=f"Tài sản cố định {r-7}",
                        org_price=Decimal(str(d)),
                        depreciation_amount=Decimal(str(ws[f'E{r}'].value or 0)),
                        accum_depreciation_amount=Decimal(str(ws[f'F{r}'].value or 0)),
                        lifetime_months=int(ws[f'G{r}'].value or 0),
                        remaining_months=int(ws[f'H{r}'].value or 0)
                    ))
            if fa_items:
                master.fixed_asset = FixedAssetAnswer(items=fa_items)

        # Parse General Balance if missing
        if not master.general_balance or not master.general_balance.accounts:
            gb_accounts = {}
            for r in range(8, 30):
                tk = ws[f'I{r}'].value
                if tk:
                    code = str(tk).strip()
                    gb_accounts[code] = AccountBalanceItem(
                        account_code=code,
                        debit_amount=Decimal(str(ws[f'J{r}'].value or 0)),
                        debit_amount_oc=Decimal(str(ws[f'K{r}'].value or 0)),
                        credit_amount=Decimal(str(ws[f'L{r}'].value or 0)),
                        credit_amount_oc=Decimal(str(ws[f'M{r}'].value or 0)),
                        quantity=Decimal(str(ws[f'N{r}'].value or 0))
                    )
            if gb_accounts:
                master.general_balance = GeneralBalanceAnswer(accounts=gb_accounts)

        # Parse Transactions if missing
        if not master.transactions or not master.transactions.entries:
            tx_entries = {}
            for r in range(8, 45):
                task = ws[f'O{r}'].value
                tk = ws[f'P{r}'].value
                if task is not None and tk is not None:
                    try:
                        task_id = int(task)
                        code = str(tk).strip()
                        tx_entries[(task_id, code)] = TransactionItem(
                            task_id=task_id,
                            account_code=code,
                            amount=Decimal(str(ws[f'Q{r}'].value or 0)),
                            amount_oc=Decimal(str(ws[f'R{r}'].value or 0)),
                            quantity=Decimal(str(ws[f'S{r}'].value or 0))
                        )
                    except (ValueError, TypeError):
                        pass
            if tx_entries:
                master.transactions = TransactionAnswer(entries=tx_entries)

        # Parse Financial Reports if missing
        if not master.financial_reports or not master.financial_reports.items:
            fr_items = {}
            for r in range(8, 40):
                rtype = ws[f'T{r}'].value
                icode = ws[f'U{r}'].value
                amt = ws[f'V{r}'].value
                if rtype is not None and icode is not None and amt is not None:
                    fr_items[(str(rtype).strip(), str(icode).strip())] = Decimal(str(amt))
            if fr_items:
                master.financial_reports = FinancialReportAnswer(items=fr_items)

        wb.close()
