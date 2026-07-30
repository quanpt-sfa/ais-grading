from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple
import logging

from src.db.connection import DatabaseConnection
from src.db.queries import QueryRepository
from src.answer.models import (
    AccountBalanceItem,
    AccountingGraph,
    FinancialReportAnswer,
    FixedAssetAnswer,
    FixedAssetItem,
    GeneralBalanceAnswer,
    InventoryAnswer,
    MasterAnswerData,
    TransactionAnswer,
    TransactionItem,
)
from src.answer.snapshot import AnswerSnapshot, AnswerSnapshotStore
from src.answer.validator import AnswerValidator
from src.grading.accounting_graph import AccountingGraphBuilder

logger = logging.getLogger(__name__)


class AnswerLoader:
    """Trích xuất một canonical answer package từ đúng một nguồn database."""

    CANONICAL_QUERY_IDS = (
        "catalog_inventory_items",
        "opening_inventory_lines",
        "fixed_asset_balance",
        "opening_general_ledger_lines",
        "purchase_order_lines",
        "purchase_voucher_lines",
        "inventory_inward_lines",
        "inventory_ledger_lines",
        "general_ledger_lines",
        "voucher_edges",
        "financial_reports",
    )

    DATE_SCOPED_QUERY_IDS = {
        "purchase_order_lines",
        "purchase_voucher_lines",
        "inventory_inward_lines",
        "inventory_ledger_lines",
        "general_ledger_lines",
    }

    def __init__(
        self,
        db_conn: DatabaseConnection,
        query_repo: QueryRepository,
        config: Dict[str, Any],
    ):
        self.db_conn = db_conn
        self.query_repo = query_repo
        self.config = config
        self.answer_config = config.get("answer", {})
        self.answer_query_repo = QueryRepository(
            self.answer_config.get(
                "query_file", "config/answer_queries.yaml"
            )
        )
        self.answer_db = self.answer_config.get("database", "HungBinh2024")
        self.source = str(
            self.answer_config.get("source", "database")
        ).strip().lower()
        self.strict = bool(self.answer_config.get("strict", True))
        self.answer_version = str(
            self.answer_config.get("version", "unversioned")
        )
        self.scope = dict(self.answer_config.get("scope", {}) or {})
        self.isolation_level = str(
            self.answer_config.get("transaction_isolation", "SERIALIZABLE")
        )
        self.graph_builder = AccountingGraphBuilder()
        validator_config = dict(
            self.answer_config.get("validation", {}) or {}
        )
        validator_config.setdefault(
            "required_queries", list(self.CANONICAL_QUERY_IDS)
        )
        self.validator = AnswerValidator(validator_config)
        self.snapshot_store = AnswerSnapshotStore(
            self.answer_config.get(
                "snapshot_directory", "data/answer_snapshots"
            )
        )

    def load(self) -> MasterAnswerData:
        if self.source != "database":
            raise RuntimeError(
                "Single-ground-truth mode requires answer.source=database. "
                "Excel may be imported into a database beforehand, but it is "
                "not allowed as an automatic fallback during grading."
            )
        if not self.db_conn.database_exists(self.answer_db):
            raise RuntimeError(
                f"Answer database is unavailable: {self.answer_db}"
            )

        queries = {
            query_id: self.answer_query_repo.get_query(query_id)
            for query_id in self.CANONICAL_QUERY_IDS
        }
        batch = {
            query_id: (query, ())
            for query_id, query in queries.items()
        }
        logger.info(
            "Trích xuất canonical answer [%s] trong một transaction %s...",
            self.answer_db,
            self.isolation_level,
        )
        raw = self.db_conn.execute_query_batch(
            self.answer_db,
            batch,
            isolation_level=self.isolation_level,
        )
        scoped_raw, scope_metrics = self._apply_scope(raw)
        graph = self.graph_builder.build(scoped_raw)
        validation = self.validator.assert_valid(
            scoped_raw,
            graph,
            self.scope,
        )
        validation.metrics["scope_excluded_rows"] = scope_metrics

        master = self._build_master(scoped_raw, graph)
        snapshot = AnswerSnapshot.create(
            answer_version=self.answer_version,
            source_database=self.answer_db,
            scope=self.scope,
            queries=queries,
            config=self.answer_config,
            validation_report=validation.to_dict(),
            raw_data=scoped_raw,
        )
        snapshot_path = self.snapshot_store.save(snapshot)
        master.answer_snapshot_id = snapshot.snapshot_id
        master.answer_version = snapshot.answer_version
        master.answer_data_hash = snapshot.data_hash
        master.answer_snapshot_path = str(snapshot_path)
        master.validation_report = validation.to_dict()
        master.raw_data = scoped_raw

        logger.info(
            "Đã khóa answer snapshot %s (%s)",
            snapshot.snapshot_id,
            snapshot_path,
        )
        return master

    def _apply_scope(
        self,
        raw: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> Tuple[Dict[str, list], Dict[str, int]]:
        start = self._date(self.scope.get("start"))
        end = self._date(self.scope.get("end"))
        branch_id = self.scope.get("branch_id")
        filtered: Dict[str, list] = {}
        excluded: Dict[str, int] = {}

        for query_id, rows in raw.items():
            output = []
            excluded_count = 0
            for row in rows:
                if branch_id and row.get("BranchID") not in (None, branch_id):
                    excluded_count += 1
                    continue
                if query_id in self.DATE_SCOPED_QUERY_IDS:
                    ref_date = self._date(
                        row.get("RefDate") or row.get("PostedDate")
                    )
                    if start and ref_date and ref_date < start:
                        excluded_count += 1
                        continue
                    if end and ref_date and ref_date > end:
                        excluded_count += 1
                        continue
                output.append(dict(row))
            filtered[query_id] = output
            excluded[query_id] = excluded_count
        return filtered, excluded

    def _build_master(
        self,
        raw: Mapping[str, Sequence[Mapping[str, Any]]],
        graph: AccountingGraph,
    ) -> MasterAnswerData:
        master = MasterAnswerData(accounting_graph=graph)
        master.inventory = self._derive_inventory(graph)
        master.fixed_asset = self._derive_fixed_assets(
            raw.get("fixed_asset_balance", ())
        )
        master.general_balance = self._derive_general_balance(
            raw.get("opening_general_ledger_lines", ())
        )
        master.transactions = self._derive_transactions(graph)
        master.financial_reports = self._derive_financial_reports(
            raw.get("financial_reports", ())
        )
        return master

    @staticmethod
    def _derive_inventory(graph: AccountingGraph) -> InventoryAnswer:
        entity_ids = {
            row.entity_id for row in graph.opening_balances if row.entity_id
        }
        return InventoryAnswer(
            item_count=len(entity_ids),
            total_qty=sum(
                (row.quantity for row in graph.opening_balances),
                Decimal("0"),
            ),
            total_amount=sum(
                (row.amount for row in graph.opening_balances),
                Decimal("0"),
            ),
        )

    @staticmethod
    def _derive_fixed_assets(
        rows: Iterable[Mapping[str, Any]],
    ) -> FixedAssetAnswer:
        items = []
        for row in rows:
            items.append(
                FixedAssetItem(
                    code=str(row.get("FixedAssetCode") or ""),
                    name=str(row.get("FixedAssetName") or ""),
                    org_price=Decimal(str(row.get("OrgPrice") or 0)),
                    depreciation_amount=Decimal(
                        str(row.get("DepreciationAmount") or 0)
                    ),
                    accum_depreciation_amount=Decimal(
                        str(row.get("AccumDepreciationAmount") or 0)
                    ),
                    lifetime_months=int(row.get("LifeTimeInMonth") or 0),
                    remaining_months=int(
                        row.get("LifeTimeRemainingInMonth") or 0
                    ),
                )
            )
        return FixedAssetAnswer(items=items)

    @staticmethod
    def _derive_general_balance(
        rows: Iterable[Mapping[str, Any]],
    ) -> GeneralBalanceAnswer:
        accounts: Dict[str, AccountBalanceItem] = {}
        for row in rows:
            number = str(row.get("AccountNumber") or "").strip()
            if not number:
                continue
            code = number[:3]
            item = accounts.setdefault(
                code,
                AccountBalanceItem(account_code=code),
            )
            item.debit_amount += Decimal(str(row.get("DebitAmount") or 0))
            item.debit_amount_oc += Decimal(
                str(row.get("DebitAmountOC") or 0)
            )
            item.credit_amount += Decimal(str(row.get("CreditAmount") or 0))
            item.credit_amount_oc += Decimal(
                str(row.get("CreditAmountOC") or 0)
            )
            item.quantity += Decimal(str(row.get("MainQuantity") or 0))
        return GeneralBalanceAnswer(accounts=accounts)

    @staticmethod
    def _derive_transactions(graph: AccountingGraph) -> TransactionAnswer:
        entries: Dict[Tuple[int, str], TransactionItem] = {}
        for row in graph.general_ledger:
            account = str(row.debit_account or "").strip()
            ref_date = AnswerLoader._date(row.ref_date)
            if not account or ref_date is None:
                continue
            task_id = ref_date.day + ref_date.month * 100
            code = account[:3]
            key = (task_id, code)
            item = entries.setdefault(
                key,
                TransactionItem(task_id=task_id, account_code=code),
            )
            item.amount += row.amount
            if account.startswith("15") or account.startswith("511"):
                item.quantity += row.quantity
        entries = {
            key: value
            for key, value in entries.items()
            if value.amount != 0 or value.quantity != 0
        }
        return TransactionAnswer(entries=entries)

    @staticmethod
    def _derive_financial_reports(
        rows: Iterable[Mapping[str, Any]],
    ) -> FinancialReportAnswer:
        items: Dict[Tuple[str, str], Decimal] = {}
        for row in rows:
            key = (
                str(row.get("ReportType") or "").strip(),
                str(row.get("ItemCode") or "").strip(),
            )
            items[key] = Decimal(str(row.get("Amount") or 0))
        return FinancialReportAnswer(items=items)

    @staticmethod
    def _date(value: Any) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return datetime.fromisoformat(str(value)[:10]).date()
        except ValueError:
            return None
