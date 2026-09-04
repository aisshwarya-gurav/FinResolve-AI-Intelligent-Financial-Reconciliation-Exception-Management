from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable

from case_management.models import Severity, Priority


SETTLEMENT_REQUIRED_COLUMNS = [
    "transaction_id",
    "utr",
    "gross_amount",
    "fee_amount",
    "tax_amount",
    "refund_amount",
    "net_amount",
    "currency",
]

BANK_REQUIRED_COLUMNS = [
    "bank_transaction_id",
    "utr",
    "amount",
    "currency",
]


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)

    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def _currency_match(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return True

    return str(left).strip().upper() == str(right).strip().upper()


def _normalize_record(record: dict) -> dict:
    if not isinstance(record, dict):
        return {}

    return {
        k: (v if v is not None else "")
        for k, v in record.items()
    }


def _settlement_net_amount(record: dict) -> Decimal:
    normalized = _normalize_record(record)

    gross = _to_decimal(
        normalized.get("gross_amount"),
        "0",
    )

    fee = _to_decimal(
        normalized.get("fee_amount"),
        "0",
    )

    tax = _to_decimal(
        normalized.get("tax_amount"),
        "0",
    )

    refund = _to_decimal(
        normalized.get("refund_amount"),
        "0",
    )

    return _quantize_money(
        gross - fee - tax + refund
    )


def _bank_amount(record: dict) -> Decimal:
    normalized = _normalize_record(record)

    return _quantize_money(
        _to_decimal(
            normalized.get("amount"),
            "0",
        )
    )


def _read_csv_records(csv_text: str) -> list[dict]:
    if not csv_text or not csv_text.strip():
        return []

    return list(
        csv.DictReader(
            io.StringIO(csv_text)
        )
    )


def validate_settlement_csv(
    csv_text: str,
) -> tuple[bool, list[str]]:
    rows = _read_csv_records(csv_text)

    if not rows:
        return False, [
            "Settlement CSV is empty or missing headers."
        ]

    missing = [
        col
        for col in SETTLEMENT_REQUIRED_COLUMNS
        if col not in rows[0]
    ]

    if missing:
        return False, [
            "Settlement CSV missing required columns: "
            + ", ".join(missing)
        ]

    return True, []


def validate_bank_csv(
    csv_text: str,
) -> tuple[bool, list[str]]:
    rows = _read_csv_records(csv_text)

    if not rows:
        return False, [
            "Bank CSV is empty or missing headers."
        ]

    missing = [
        col
        for col in BANK_REQUIRED_COLUMNS
        if col not in rows[0]
    ]

    if missing:
        return False, [
            "Bank CSV missing required columns: "
            + ", ".join(missing)
        ]

    return True, []


@dataclass(frozen=True)
class SettlementMatchRule:
    name: str = "utr_then_net_amount"

    def matches(
        self,
        settlement: dict,
        bank_record: dict,
    ) -> bool:

        settlement = _normalize_record(settlement)
        bank_record = _normalize_record(bank_record)

        if not settlement or not bank_record:
            return False

        settlement_utr = str(
            settlement.get("utr") or ""
        ).strip()

        bank_utr = str(
            bank_record.get("utr") or ""
        ).strip()

        if self.name == "utr_then_net_amount":

            if (
                settlement_utr
                and bank_utr
                and settlement_utr != bank_utr
            ):
                return False

            if not _currency_match(
                settlement.get("currency"),
                bank_record.get("currency"),
            ):
                return False

            return (
                abs(
                    _settlement_net_amount(settlement)
                    - _bank_amount(bank_record)
                )
                <= Decimal("0.01")
            )

        if self.name == "utr_then_amount":

            if (
                settlement_utr
                and bank_utr
                and settlement_utr != bank_utr
            ):
                return False

            if not _currency_match(
                settlement.get("currency"),
                bank_record.get("currency"),
            ):
                return False

            gross = _quantize_money(
                _to_decimal(
                    settlement.get("gross_amount"),
                    "0",
                )
            )

            return (
                abs(
                    gross
                    - _bank_amount(bank_record)
                )
                <= Decimal("0.01")
            )

        # Fallback: match on UTR only if present,
        # otherwise allow ID pairing.
        if settlement_utr and bank_utr:
            return settlement_utr == bank_utr

        return (
            str(
                settlement.get("transaction_id")
                or ""
            ).strip()
            ==
            str(
                bank_record.get("bank_transaction_id")
                or ""
            ).strip()
        )


SETTLEMENT_RULES: dict[str, SettlementMatchRule] = {
    "utr_then_net_amount": SettlementMatchRule(
        "utr_then_net_amount"
    ),
    "utr_then_amount": SettlementMatchRule(
        "utr_then_amount"
    ),
    "transaction_id_fallback": SettlementMatchRule(
        "transaction_id_fallback"
    ),
}


def run_settlement_reconciliation(
    settlement_records: Iterable[dict],
    bank_records: Iterable[dict],
    rule_name: str = "utr_then_net_amount",
) -> dict:

    settlement_list = list(
        settlement_records or []
    )

    bank_list = list(
        bank_records or []
    )

    if rule_name not in SETTLEMENT_RULES:
        raise ValueError(
            f"Unknown settlement rule '{rule_name}'."
        )

    rule = SETTLEMENT_RULES[rule_name]

    matched = []
    matched_bank_ids = set()

    for settlement in settlement_list:

        settlement_record = _normalize_record(
            settlement
        )

        bank_match = None

        for bank_record in bank_list:

            if (
                bank_record.get("bank_transaction_id")
                in matched_bank_ids
            ):
                continue

            if rule.matches(
                settlement_record,
                bank_record,
            ):
                bank_match = bank_record
                break

        if bank_match is None:

            unmatched = {
                "transaction_id": settlement_record.get(
                    "transaction_id"
                ),
                "utr": settlement_record.get("utr"),
                "net_amount": settlement_record.get(
                    "net_amount"
                ),
                "gross_amount": settlement_record.get(
                    "gross_amount"
                ),
                "fee_amount": settlement_record.get(
                    "fee_amount"
                ),
                "tax_amount": settlement_record.get(
                    "tax_amount"
                ),
                "refund_amount": settlement_record.get(
                    "refund_amount"
                ),
                "currency": settlement_record.get(
                    "currency"
                ),
                "issue": "settlement_unmatched",
            }

            matched.append(
                {
                    "status": "unmatched",
                    "settlement": unmatched,
                }
            )

            continue

        matched_bank_ids.add(
            bank_match.get("bank_transaction_id")
        )

        matched.append(
            {
                "status": "matched",
                "settlement_transaction_id":
                    settlement_record.get(
                        "transaction_id"
                    ),
                "bank_transaction_id":
                    bank_match.get(
                        "bank_transaction_id"
                    ),
                "utr":
                    settlement_record.get("utr")
                    or bank_match.get("utr"),
                "settlement_net_amount":
                    str(
                        _settlement_net_amount(
                            settlement_record
                        )
                    ),
                "bank_amount":
                    str(
                        _bank_amount(bank_match)
                    ),
                "currency":
                    settlement_record.get("currency")
                    or bank_match.get("currency"),
                "rule_used": rule_name,
            }
        )

    unmatched_settlement = [
        item["settlement"]
        for item in matched
        if item["status"] == "unmatched"
    ]

    matched_results = [
        item
        for item in matched
        if item["status"] == "matched"
    ]

    unmatched_bank = []

    for bank_record in bank_list:

        bank_id = bank_record.get(
            "bank_transaction_id"
        )

        if bank_id not in matched_bank_ids:

            unmatched_bank.append(
                {
                    "bank_transaction_id": bank_id,
                    "utr": bank_record.get("utr"),
                    "amount": bank_record.get("amount"),
                    "currency": bank_record.get(
                        "currency"
                    ),
                    "issue": "bank_unmatched",
                }
            )

    return {
        "matched": matched_results,
        "unmatched_settlement":
            unmatched_settlement,
        "unmatched_bank":
            unmatched_bank,
        "rule_used":
            rule_name,
        "summary": {
            "matched":
                len(matched_results),
            "unmatched_settlement":
                len(unmatched_settlement),
            "unmatched_bank":
                len(unmatched_bank),
        },
    }


def _case_id_for_settlement(
    record: dict,
) -> str:

    return str(
        record.get("transaction_id")
        or record.get("utr")
        or record.get("bank_transaction_id")
        or "settlement_exception"
    )


def ingest_settlement_exceptions(
    store,
    pipeline_result: dict,
    actor: str = "system",
) -> list:

    created_case_ids = []

    if not hasattr(store, "create_case"):
        return created_case_ids

    for record in pipeline_result.get(
        "unmatched_settlement",
        [],
    ):

        case_id = _case_id_for_settlement(
            record
        )

        try:
            store.get_case(case_id)
            continue
        except Exception:
            pass

        amount = _to_decimal(
            record.get("net_amount"),
            "0",
        )

        if abs(amount) >= Decimal("10000"):
            severity = Severity.CRITICAL

        elif abs(amount) >= Decimal("1000"):
            severity = Severity.HIGH

        elif abs(amount) >= Decimal("100"):
            severity = Severity.MEDIUM

        else:
            severity = Severity.LOW

        priority = {
            Severity.CRITICAL: Priority.HIGH,
            Severity.HIGH: Priority.HIGH,
            Severity.MEDIUM: Priority.MEDIUM,
            Severity.LOW: Priority.LOW,
        }[severity]

        store.create_case(
            case_id=case_id,
            source_pipeline="razorpay_settlement",
            exception_code=record.get(
                "issue",
                "settlement_unmatched",
            ),
            severity=severity,
            priority=priority,
            record={
                "settlement_record": record,
                "rule_used":
                    pipeline_result.get(
                        "rule_used"
                    ),
                "unmatched_bank":
                    pipeline_result.get(
                        "unmatched_bank",
                        [],
                    ),
            },
            actor=actor,
        )

        created_case_ids.append(case_id)

    return created_case_ids