from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.answer.models import AccountingEventLine, AccountingGraph


class AnswerValidationError(RuntimeError):
    pass


@dataclass
class AnswerValidationReport:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["is_valid"] = self.is_valid
        return result


class AnswerValidator:
    """Kiểm tra tính đầy đủ và nhất quán trước khi đóng băng đáp án."""

    EVENT_QUERIES = (
        "purchase_order_lines",
        "purchase_voucher_lines",
        "inventory_inward_lines",
    )
    DATE_SCOPED_QUERY_IDS = (
        "purchase_order_lines",
        "purchase_voucher_lines",
        "inventory_inward_lines",
        "inventory_ledger_lines",
        "general_ledger_lines",
    )
    REPORT_HEADER_FIELDS = (
        "ReportRefType",
        "DisplayOnBook",
        "BranchID",
        "Period",
        "Year",
        "FromDate",
        "ToDate",
        "CurrencyID",
        "IsReportFinanceAudit",
    )

    def __init__(self, config: Optional[Mapping[str, Any]] = None):
        cfg = dict(config or {})
        self.required_queries = tuple(cfg.get("required_queries", ()))
        self.required_workflow_roles = tuple(
            cfg.get("required_workflow_roles", ())
        )
        self.require_scope = bool(cfg.get("require_scope", True))
        self.require_postings = bool(cfg.get("require_postings", True))
        self.require_financial_reports = bool(
            cfg.get("require_financial_reports", True)
        )

    def validate(
        self,
        raw: Mapping[str, Sequence[Mapping[str, Any]]],
        graph: AccountingGraph,
        scope: Mapping[str, Any],
    ) -> AnswerValidationReport:
        report = AnswerValidationReport()
        self._validate_scope(scope, report)
        self._validate_required_queries(raw, report)
        self._validate_row_scope(raw, scope, report)
        self._validate_duplicate_keys(raw, report)
        self._validate_financial_reports(raw.get("financial_reports", ()), report)
        self._validate_graph(graph, report)
        report.metrics.update(
            {
                "entity_count": len(graph.entities),
                "opening_balance_count": len(graph.opening_balances),
                "workflow_event_count": len(graph.events),
                "inventory_posting_count": len(graph.inventory_ledger),
                "general_ledger_posting_count": len(graph.general_ledger),
                "voucher_edge_count": len(graph.edges),
            }
        )
        return report

    def assert_valid(
        self,
        raw: Mapping[str, Sequence[Mapping[str, Any]]],
        graph: AccountingGraph,
        scope: Mapping[str, Any],
    ) -> AnswerValidationReport:
        report = self.validate(raw, graph, scope)
        if report.errors:
            joined = "\n- ".join(report.errors)
            raise AnswerValidationError(
                f"Canonical answer validation failed:\n- {joined}"
            )
        return report

    def _validate_scope(
        self,
        scope: Mapping[str, Any],
        report: AnswerValidationReport,
    ) -> None:
        if not self.require_scope:
            return
        if not scope.get("start") or not scope.get("end"):
            report.errors.append(
                "answer.scope.start and answer.scope.end are required in strict mode"
            )
            return
        start = self._date(scope.get("start"))
        end = self._date(scope.get("end"))
        if start is None or end is None:
            report.errors.append("answer scope contains an invalid date")
        elif start > end:
            report.errors.append("answer.scope.start must not be after scope.end")

    def _validate_required_queries(
        self,
        raw: Mapping[str, Sequence[Mapping[str, Any]]],
        report: AnswerValidationReport,
    ) -> None:
        for query_id in self.required_queries:
            if query_id not in raw:
                report.errors.append(f"required answer query missing: {query_id}")
        if self.require_financial_reports and not raw.get("financial_reports"):
            report.errors.append("financial_reports returned no answer rows")

    def _validate_row_scope(
        self,
        raw: Mapping[str, Sequence[Mapping[str, Any]]],
        scope: Mapping[str, Any],
        report: AnswerValidationReport,
    ) -> None:
        start = self._date(scope.get("start"))
        end = self._date(scope.get("end"))
        for query_id in self.DATE_SCOPED_QUERY_IDS:
            for index, row in enumerate(raw.get(query_id, ())):
                ref_date = self._date(
                    row.get("RefDate") or row.get("PostedDate")
                )
                if ref_date is None:
                    report.errors.append(
                        f"{query_id}[{index}] has no valid RefDate/PostedDate"
                    )
                    continue
                if start and ref_date < start:
                    report.errors.append(
                        f"{query_id}[{index}] is before answer scope"
                    )
                if end and ref_date > end:
                    report.errors.append(
                        f"{query_id}[{index}] is after answer scope"
                    )

        for index, row in enumerate(raw.get("financial_reports", ())):
            report_from = self._date(row.get("FromDate"))
            report_to = self._date(row.get("ToDate"))
            report_year = self._int_or_none(row.get("Year"))
            if report_from and report_to and report_from > report_to:
                report.errors.append(
                    f"financial_reports[{index}] has FromDate after ToDate"
                )
            if report_from is None and report_to is None and report_year is None:
                report.warnings.append(
                    f"financial_reports[{index}] has no period metadata; "
                    "cross-database matching will use a reduced key"
                )

    def _validate_duplicate_keys(
        self,
        raw: Mapping[str, Sequence[Mapping[str, Any]]],
        report: AnswerValidationReport,
    ) -> None:
        checks = {
            "catalog_inventory_items": ("InventoryItemID",),
            "opening_inventory_lines": (
                "InventoryItemID",
                "StockID",
                "CanonicalUnitID",
            ),
            "fixed_asset_balance": ("FixedAssetCode",),
            "opening_general_ledger_lines": ("GeneralLedgerID",),
            "purchase_order_lines": ("RefID", "RefDetailID"),
            "purchase_voucher_lines": ("RefID", "RefDetailID"),
            "inventory_inward_lines": ("RefID", "RefDetailID"),
            "inventory_ledger_lines": ("InventoryLedgerID",),
            "general_ledger_lines": ("GeneralLedgerID",),
            # ReportType + ItemCode không duy nhất. Chỉ khóa kỹ thuật dòng được
            # phép quyết định trùng dữ liệu trong snapshot cục bộ.
            "financial_reports": ("ReportDetailID",),
        }
        for query_id, fields in checks.items():
            rows = raw.get(query_id, ())
            duplicates = self._duplicates(rows, fields)
            if duplicates:
                sample = ", ".join(repr(key) for key in duplicates[:3])
                report.errors.append(
                    f"duplicate canonical key in {query_id}: {sample}"
                )

    def _validate_financial_reports(
        self,
        rows: Sequence[Mapping[str, Any]],
        report: AnswerValidationReport,
    ) -> None:
        instance_ids = set()
        report_type_item_counts: Dict[Tuple[Any, Any], int] = {}
        header_by_ref: Dict[Any, Tuple[Any, ...]] = {}

        for index, row in enumerate(rows):
            detail_id = row.get("ReportDetailID")
            ref_id = row.get("RefID")
            if detail_id in (None, ""):
                report.errors.append(
                    f"financial_reports[{index}] has no ReportDetailID"
                )
            if ref_id in (None, ""):
                report.errors.append(
                    f"financial_reports[{index}] has no RefID/report instance"
                )
            else:
                instance_ids.add(str(ref_id))
                header = tuple(row.get(field) for field in self.REPORT_HEADER_FIELDS)
                previous = header_by_ref.setdefault(ref_id, header)
                if previous != header:
                    report.errors.append(
                        f"financial report RefID {ref_id} has inconsistent header metadata"
                    )

            business_key = (row.get("ReportType"), row.get("ItemCode"))
            report_type_item_counts[business_key] = (
                report_type_item_counts.get(business_key, 0) + 1
            )

        repeated_groups = {
            key: count
            for key, count in report_type_item_counts.items()
            if count > 1
        }
        if repeated_groups:
            report.warnings.append(
                "ReportType + ItemCode is non-unique in financial_reports; "
                "multiplicity is preserved and graded as report-line multisets"
            )

        report.metrics.update(
            {
                "financial_report_line_count": len(rows),
                "financial_report_instance_count": len(instance_ids),
                "non_unique_report_type_item_code_groups": len(repeated_groups),
            }
        )

    def _validate_graph(
        self,
        graph: AccountingGraph,
        report: AnswerValidationReport,
    ) -> None:
        if not graph.entities:
            report.errors.append("answer catalog contains no inventory entities")
        if not graph.opening_balances:
            report.warnings.append("answer contains no opening inventory balances")

        roles = {event.module_role for event in graph.events}
        for required_role in self.required_workflow_roles:
            if required_role not in roles:
                report.errors.append(
                    f"required workflow role is absent: {required_role}"
                )

        for event in graph.events:
            if event.entity_id and event.entity_id not in graph.entities:
                report.errors.append(
                    f"event {event.ref_id}/{event.ref_detail_id} references "
                    f"unknown InventoryItemID {event.entity_id}"
                )

        detail_ids = {
            event.ref_detail_id
            for event in graph.events
            if event.ref_detail_id
        }
        for event in graph.events:
            if (
                event.source_ref_detail_id
                and event.source_ref_detail_id not in detail_ids
            ):
                report.errors.append(
                    f"broken source detail link: {event.source_ref_detail_id} "
                    f"from {event.ref_id}/{event.ref_detail_id}"
                )

        if self.require_postings:
            for event in graph.events:
                if event.is_posted_inventory is True and not self._has_posting(
                    graph.inventory_ledger, event
                ):
                    report.errors.append(
                        f"posted inventory event has no InventoryLedger row: "
                        f"{event.ref_id}/{event.ref_detail_id}"
                    )
                if event.is_posted_finance is True and not self._has_posting(
                    graph.general_ledger, event
                ):
                    report.errors.append(
                        f"posted finance event has no GeneralLedger row: "
                        f"{event.ref_id}/{event.ref_detail_id}"
                    )

    @staticmethod
    def _duplicates(
        rows: Iterable[Mapping[str, Any]],
        fields: Tuple[str, ...],
    ) -> List[Tuple[Any, ...]]:
        seen = set()
        duplicates: List[Tuple[Any, ...]] = []
        for row in rows:
            key = tuple(row.get(field) for field in fields)
            if key in seen and key not in duplicates:
                duplicates.append(key)
            seen.add(key)
        return duplicates

    @staticmethod
    def _has_posting(
        postings: Iterable[AccountingEventLine],
        event: AccountingEventLine,
    ) -> bool:
        for posting in postings:
            if posting.ref_id != event.ref_id:
                continue
            if event.entity_id and posting.entity_id not in (None, event.entity_id):
                continue
            if (
                event.ref_detail_id
                and posting.ref_detail_id
                and event.ref_detail_id != posting.ref_detail_id
            ):
                continue
            return True
        return False

    @staticmethod
    def _int_or_none(value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

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
