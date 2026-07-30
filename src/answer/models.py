from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Tuple, Any, Optional

@dataclass
class InventoryAnswer:
    item_count: int = 0
    total_qty: Decimal = Decimal('0')
    total_amount: Decimal = Decimal('0')

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
    debit_amount: Decimal = Decimal('0')
    debit_amount_oc: Decimal = Decimal('0')
    credit_amount: Decimal = Decimal('0')
    credit_amount_oc: Decimal = Decimal('0')
    quantity: Decimal = Decimal('0')

@dataclass
class GeneralBalanceAnswer:
    accounts: Dict[str, AccountBalanceItem] = field(default_factory=dict)

@dataclass
class TransactionItem:
    task_id: int
    account_code: str
    amount: Decimal = Decimal('0')
    amount_oc: Decimal = Decimal('0')
    quantity: Decimal = Decimal('0')

@dataclass
class TransactionAnswer:
    entries: Dict[Tuple[int, str], TransactionItem] = field(default_factory=dict)

@dataclass
class FinancialReportAnswer:
    items: Dict[Tuple[str, str], Decimal] = field(default_factory=dict)  # (ReportType, ItemCode) -> Amount

@dataclass
class MasterAnswerData:
    inventory: Optional[InventoryAnswer] = None
    fixed_asset: Optional[FixedAssetAnswer] = None
    general_balance: Optional[GeneralBalanceAnswer] = None
    transactions: Optional[TransactionAnswer] = None
    financial_reports: Optional[FinancialReportAnswer] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)
