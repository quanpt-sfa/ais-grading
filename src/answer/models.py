from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Mapping, Tuple, Any, Optional


@dataclass
class InventoryAnswer:
    item_count: int = 0
    total_qty: Decimal = Decimal("0")
    total_amount: Decimal = Decimal("0")


@dataclass
class FixedAssetItem:
    code: str
    name: str
    org_price: Decimal
    depreciation_amount: Decimal
    accum_depreciation_amount: Decimal
    lifetime_months: int
    remaining_months: int


@dataclass
class FixedAssetAnswer:
    items: List[FixedAssetItem] = field(default_factory=list)


@dataclass
class AccountBalanceItem:
    account_code: str
    debit_amount: Decimal = Decimal("0")
    debit_amount_oc: Decimal = Decimal("0")
    credit_amount: Decimal = Decimal("0")
    credit_amount_oc: Decimal = Decimal("0")
    quantity: Decimal = Decimal("0")


@dataclass
class GeneralBalanceAnswer:
    accounts: Dict[str, AccountBalanceItem] = field(default_factory=dict)


@dataclass
class TransactionItem:
    task_id: int
    account_code: str
    amount: Decimal = Decimal("0")
    amount_oc: Decimal = Decimal("0")
    quantity: Decimal = Decimal("0")


@dataclass
class TransactionAnswer:
    entries: Dict[Tuple[int, str], TransactionItem] = field(default_factory=dict)


@dataclass(frozen=True)
class FinancialReportLine:
    """Một dòng của một report instance trong database cục bộ.

    ``ReportDetailID`` và ``report_ref_id`` chỉ là khóa kỹ thuật bên trong một
    database. Chúng được giữ để audit/snapshot, nhưng không được so trực tiếp
    giữa database đáp án và database sinh viên.
    """

    report_detail_id: Optional[str]
    report_ref_id: Optional[str]
    report_type: str
    item_id: Optional[str]
    item_code: str
    item_index: Optional[int]
    sort_order: Optional[int]
    category: Optional[int]
    formula_type: Optional[int]
    amount: Decimal
    prev_amount: Decimal = Decimal("0")
    other_amount: Decimal = Decimal("0")
    other_prev_amount: Decimal = Decimal("0")
    report_ref_type: Optional[int] = None
    display_on_book: Optional[int] = None
    branch_id: Optional[str] = None
    period: Optional[int] = None
    year: Optional[int] = None
    period_name: Optional[str] = None
    report_name: Optional[str] = None
    from_date: Any = None
    to_date: Any = None
    currency_id: Optional[str] = None
    is_report_finance_audit: Optional[bool] = None


@dataclass(init=False)
class FinancialReportAnswer:
    # Dùng list để bảo toàn multiplicity. ReportType + ItemCode không duy nhất.
    lines: List[FinancialReportLine]

    def __init__(
        self,
        lines: Optional[List[FinancialReportLine]] = None,
        items: Optional[Mapping[Tuple[str, str], Decimal]] = None,
    ):
        if lines is not None and items is not None:
            raise ValueError("Pass either financial-report lines or legacy items")
        if lines is not None:
            self.lines = list(lines)
            return

        # Đọc cache/API cũ. Mapping cũ vốn không thể biểu diễn duplicate; dữ liệu
        # mới luôn phải đi qua ``lines`` hoặc immutable answer snapshot.
        self.lines = []
        for key, amount in (items or {}).items():
            report_type, item_code = key[:2]
            self.lines.append(
                FinancialReportLine(
                    report_detail_id=None,
                    report_ref_id=None,
                    report_type=str(report_type),
                    item_id=None,
                    item_code=str(item_code),
                    item_index=None,
                    sort_order=None,
                    category=None,
                    formula_type=None,
                    amount=Decimal(str(amount)),
                )
            )

    @property
    def items(self) -> List[FinancialReportLine]:
        """Alias đọc tương thích; không còn là dictionary."""
        return self.lines


@dataclass(frozen=True)
class InventoryEntity:
    """Mặt hàng cục bộ trong một database kế toán.

    GUID chỉ dùng để theo dõi bên trong database đó, không dùng để đối chiếu
    trực tiếp giữa database đáp án và database sinh viên.
    """

    entity_id: str
    item_type: Optional[int] = None
    unit_name: Optional[str] = None
    display_code: str = ""
    display_name: str = ""
    inventory_account: Optional[str] = None
    cogs_account: Optional[str] = None
    sale_account: Optional[str] = None
    tax_rate: Decimal = Decimal("0")


@dataclass(frozen=True)
class OpeningInventoryLine:
    entity_id: str
    stock_id: Optional[str]
    unit_id: Optional[str]
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal


@dataclass(frozen=True)
class AccountingEventLine:
    module_role: str
    ref_type: int
    ref_id: str
    ref_detail_id: Optional[str]
    ref_date: Any
    posted_date: Any
    entity_id: Optional[str]
    account_object_id: Optional[str]
    stock_id: Optional[str]
    unit_id: Optional[str]
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    debit_account: Optional[str] = None
    credit_account: Optional[str] = None
    source_ref_id: Optional[str] = None
    source_ref_detail_id: Optional[str] = None
    is_posted_finance: Optional[bool] = None
    is_posted_inventory: Optional[bool] = None


@dataclass(frozen=True)
class EventEdge:
    source_ref_id: str
    target_ref_id: str
    source_ref_type: Optional[int] = None
    target_ref_type: Optional[int] = None
    reference_type: Optional[int] = None
    evidence_type: str = "EXPLICIT_HEADER_LINK"


@dataclass
class AccountingGraph:
    entities: Dict[str, InventoryEntity] = field(default_factory=dict)
    opening_balances: List[OpeningInventoryLine] = field(default_factory=list)
    events: List[AccountingEventLine] = field(default_factory=list)
    inventory_ledger: List[AccountingEventLine] = field(default_factory=list)
    general_ledger: List[AccountingEventLine] = field(default_factory=list)
    edges: List[EventEdge] = field(default_factory=list)
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_workflow_data(self) -> bool:
        return bool(self.events)


@dataclass
class MasterAnswerData:
    inventory: Optional[InventoryAnswer] = None
    fixed_asset: Optional[FixedAssetAnswer] = None
    general_balance: Optional[GeneralBalanceAnswer] = None
    transactions: Optional[TransactionAnswer] = None
    financial_reports: Optional[FinancialReportAnswer] = None
    accounting_graph: Optional[AccountingGraph] = None
    answer_snapshot_id: Optional[str] = None
    answer_version: Optional[str] = None
    answer_data_hash: Optional[str] = None
    answer_snapshot_path: Optional[str] = None
    validation_report: Dict[str, Any] = field(default_factory=dict)
    raw_data: Dict[str, Any] = field(default_factory=dict)
