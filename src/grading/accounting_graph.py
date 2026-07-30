from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional

from src.answer.models import (
    AccountingEventLine,
    AccountingGraph,
    EventEdge,
    InventoryEntity,
    OpeningInventoryLine,
)


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value))


def _text(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value)


def _bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    return bool(value)


def _canonical_quantity(row: Mapping[str, Any], *, inward: bool = False) -> Decimal:
    if inward:
        main = _decimal(row.get("MainInwardQuantity"))
        raw = _decimal(row.get("InwardQuantity"))
    else:
        main = _decimal(row.get("MainQuantity"))
        raw = _decimal(row.get("Quantity"))
    return main if main != 0 else raw


def _canonical_unit_price(row: Mapping[str, Any], *keys: str) -> Decimal:
    for key in keys:
        value = _decimal(row.get(key))
        if value != 0:
            return value
    return Decimal("0")


class AccountingGraphBuilder:
    """Chuyển các query cấp dòng thành mô hình đồ thị kế toán chuẩn hóa."""

    QUERY_IDS = (
        "catalog_inventory_items",
        "opening_inventory_lines",
        "purchase_order_lines",
        "purchase_voucher_lines",
        "inventory_inward_lines",
        "inventory_ledger_lines",
        "general_ledger_lines",
        "voucher_edges",
    )

    EVENT_QUERY_ROLES = {
        "purchase_order_lines": "PU_ORDER",
        "purchase_voucher_lines": "PU_VOUCHER",
        "inventory_inward_lines": "IN_INWARD",
    }

    def build(self, raw: Dict[str, List[Dict[str, Any]]]) -> AccountingGraph:
        graph = AccountingGraph(raw_data=raw)
        self._add_entities(graph, raw.get("catalog_inventory_items", []))
        self._add_opening_balances(graph, raw.get("opening_inventory_lines", []))

        for query_id, module_role in self.EVENT_QUERY_ROLES.items():
            graph.events.extend(
                self._build_events(raw.get(query_id, []), module_role)
            )

        graph.inventory_ledger.extend(
            self._build_inventory_ledger(raw.get("inventory_ledger_lines", []))
        )
        graph.general_ledger.extend(
            self._build_general_ledger(raw.get("general_ledger_lines", []))
        )
        graph.edges.extend(self._build_edges(raw.get("voucher_edges", [])))
        return graph

    def _add_entities(
        self,
        graph: AccountingGraph,
        rows: Iterable[Mapping[str, Any]],
    ) -> None:
        for row in rows:
            entity_id = _text(row.get("InventoryItemID"))
            if not entity_id:
                continue
            graph.entities[entity_id] = InventoryEntity(
                entity_id=entity_id,
                item_type=(
                    int(row["InventoryItemType"])
                    if row.get("InventoryItemType") is not None
                    else None
                ),
                unit_name=(
                    str(row.get("UnitName")).strip().casefold()
                    if row.get("UnitName")
                    else None
                ),
                display_code=str(row.get("InventoryItemCode") or ""),
                display_name=str(row.get("InventoryItemName") or ""),
                inventory_account=_text(row.get("InventoryAccount")),
                cogs_account=_text(row.get("COGSAccount")),
                sale_account=_text(row.get("SaleAccount")),
                tax_rate=_decimal(row.get("TaxRate")),
            )

    def _add_opening_balances(
        self,
        graph: AccountingGraph,
        rows: Iterable[Mapping[str, Any]],
    ) -> None:
        for row in rows:
            entity_id = _text(row.get("InventoryItemID"))
            if not entity_id:
                continue
            graph.opening_balances.append(
                OpeningInventoryLine(
                    entity_id=entity_id,
                    stock_id=_text(row.get("StockID")),
                    unit_id=_text(row.get("CanonicalUnitID") or row.get("UnitID")),
                    quantity=_decimal(row.get("OpeningQuantity")),
                    unit_price=_decimal(row.get("OpeningUnitPrice")),
                    amount=_decimal(row.get("OpeningAmount")),
                )
            )

    def _build_events(
        self,
        rows: Iterable[Mapping[str, Any]],
        module_role: str,
    ) -> List[AccountingEventLine]:
        events: List[AccountingEventLine] = []
        for row in rows:
            ref_id = _text(row.get("RefID"))
            if not ref_id:
                continue

            quantity = _canonical_quantity(row)
            unit_price = _canonical_unit_price(
                row,
                "MainUnitPrice",
                "MainUnitPriceFinance",
                "UnitPrice",
                "UnitPriceFinance",
            )
            amount = _canonical_unit_price(
                row,
                "Amount",
                "InwardAmount",
                "AmountFinance",
            )
            events.append(
                AccountingEventLine(
                    module_role=str(row.get("ModuleRole") or module_role),
                    ref_type=int(row.get("RefType") or 0),
                    ref_id=ref_id,
                    ref_detail_id=_text(row.get("RefDetailID")),
                    ref_date=row.get("RefDate"),
                    posted_date=row.get("PostedDate"),
                    entity_id=_text(row.get("InventoryItemID")),
                    account_object_id=_text(row.get("AccountObjectID")),
                    stock_id=_text(row.get("StockID")),
                    unit_id=_text(row.get("MainUnitID") or row.get("UnitID")),
                    quantity=quantity,
                    unit_price=unit_price,
                    amount=amount,
                    debit_account=_text(row.get("DebitAccount")),
                    credit_account=_text(row.get("CreditAccount")),
                    source_ref_id=_text(row.get("SourceRefID")),
                    source_ref_detail_id=_text(row.get("SourceRefDetailID")),
                    is_posted_finance=_bool(row.get("IsPostedFinance")),
                    is_posted_inventory=_bool(row.get("IsPostedInventory")),
                )
            )
        return events

    def _build_inventory_ledger(
        self,
        rows: Iterable[Mapping[str, Any]],
    ) -> List[AccountingEventLine]:
        result: List[AccountingEventLine] = []
        for row in rows:
            ref_id = _text(row.get("RefID"))
            if not ref_id:
                continue
            inward_qty = _canonical_quantity(row, inward=True)
            outward_main = _decimal(row.get("MainOutwardQuantity"))
            outward_raw = _decimal(row.get("OutwardQuantity"))
            outward_qty = outward_main if outward_main != 0 else outward_raw
            inward_amount = _decimal(row.get("InwardAmount"))
            outward_amount = _decimal(row.get("OutwardAmount"))
            result.append(
                AccountingEventLine(
                    module_role="INVENTORY_LEDGER",
                    ref_type=int(row.get("RefType") or 0),
                    ref_id=ref_id,
                    ref_detail_id=_text(row.get("RefDetailID")),
                    ref_date=row.get("RefDate"),
                    posted_date=row.get("PostedDate"),
                    entity_id=_text(row.get("InventoryItemID")),
                    account_object_id=_text(row.get("AccountObjectID")),
                    stock_id=_text(row.get("StockID")),
                    unit_id=_text(row.get("MainUnitID") or row.get("UnitID")),
                    quantity=inward_qty - outward_qty,
                    unit_price=_canonical_unit_price(
                        row, "MainUnitPrice", "UnitPrice"
                    ),
                    amount=inward_amount - outward_amount,
                    debit_account=_text(row.get("AccountNumber")),
                    credit_account=_text(row.get("CorrespondingAccountNumber")),
                    source_ref_id=_text(row.get("ConfrontingRefID")),
                    source_ref_detail_id=_text(row.get("ConfrontingRefDetailID")),
                    is_posted_inventory=True,
                )
            )
        return result

    def _build_general_ledger(
        self,
        rows: Iterable[Mapping[str, Any]],
    ) -> List[AccountingEventLine]:
        result: List[AccountingEventLine] = []
        for row in rows:
            ref_id = _text(row.get("RefID"))
            if not ref_id:
                continue
            debit = _decimal(row.get("DebitAmount"))
            credit = _decimal(row.get("CreditAmount"))
            result.append(
                AccountingEventLine(
                    module_role="GENERAL_LEDGER",
                    ref_type=int(row.get("RefType") or 0),
                    ref_id=ref_id,
                    ref_detail_id=_text(row.get("RefDetailID")),
                    ref_date=row.get("RefDate"),
                    posted_date=row.get("PostedDate"),
                    entity_id=_text(row.get("InventoryItemID")),
                    account_object_id=_text(row.get("AccountObjectID")),
                    stock_id=_text(row.get("StockID")),
                    unit_id=_text(row.get("MainUnitID") or row.get("UnitID")),
                    quantity=_canonical_quantity(row),
                    unit_price=_canonical_unit_price(
                        row, "MainUnitPrice", "UnitPrice"
                    ),
                    amount=debit - credit,
                    debit_account=_text(row.get("AccountNumber")),
                    credit_account=_text(row.get("CorrespondingAccountNumber")),
                    source_ref_id=_text(row.get("PUOrderRefID")),
                    is_posted_finance=True,
                )
            )
        return result

    def _build_edges(
        self,
        rows: Iterable[Mapping[str, Any]],
    ) -> List[EventEdge]:
        edges: List[EventEdge] = []
        for row in rows:
            source = _text(row.get("RefID1"))
            target = _text(row.get("RefID2"))
            if not source or not target:
                continue
            edges.append(
                EventEdge(
                    source_ref_id=source,
                    target_ref_id=target,
                    source_ref_type=(
                        int(row["RefType1"])
                        if row.get("RefType1") is not None
                        else None
                    ),
                    target_ref_type=(
                        int(row["RefType2"])
                        if row.get("RefType2") is not None
                        else None
                    ),
                    reference_type=(
                        int(row["ReferenceType"])
                        if row.get("ReferenceType") is not None
                        else None
                    ),
                )
            )
        return edges
