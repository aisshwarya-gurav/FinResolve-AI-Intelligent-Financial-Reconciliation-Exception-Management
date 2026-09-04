"""
Normalizes a raw Razorpay Transaction response into this project's
internal financial record format.

Field mapping (Razorpay paise -> our decimal amounts, matching how every
other pipeline in this project represents money, e.g. finrca_data's
payment_amount: 2863.66, not 286366):

  transaction_id  <- raw.id                          (e.g. "txn_...")
  source_id       <- raw.source.id                    (e.g. "pout_...")
  source_type     <- passed in explicitly (payout | bank_transfer)
  amount          <- raw.amount   / 100
  currency        <- raw.currency
  debit           <- raw.debit    / 100
  credit          <- raw.credit   / 100
  fees            <- raw.source.fees / 100  (0.0 if absent — bank_transfer
                       sources don't carry a fee field per Razorpay's docs)
  tax             <- raw.source.tax  / 100  (0.0 if absent, same reason)
  status          <- raw.source.status, or "settled" if absent — a
                       transaction record is by definition a completed
                       ledger entry, so "settled" is a reasonable default
                       for source types (like bank_transfer here) that
                       don't carry their own status field
  utr             <- raw.utr (top-level; docs confirm this is only
                       populated when source is 'payout')
  mode            <- raw.mode (top-level; same 'payout'-only caveat)
  created_at      <- raw.created_at, converted from Unix timestamp to
                       ISO 8601, matching date formatting used elsewhere
                       in this project (e.g. finrca_data's "2025-02-21")
"""

from datetime import datetime, timezone

VALID_SOURCE_TYPES = {"payout", "bank_transfer"}


class NormalizationError(Exception):
    pass


def normalize_transaction(raw: dict, source_type: str) -> dict:
    if source_type not in VALID_SOURCE_TYPES:
        raise NormalizationError(f"Unknown source_type '{source_type}'. Use 'payout' or 'bank_transfer'.")
    if not isinstance(raw, dict):
        raise NormalizationError("Raw transaction must be a dict.")
    if "id" not in raw:
        raise NormalizationError("Raw transaction is missing required field 'id'.")

    source = raw.get("source") or {}

    def _to_decimal(paise_value) -> float:
        if paise_value is None:
            return 0.0
        try:
            return round(float(paise_value) / 100, 2)
        except (TypeError, ValueError):
            return 0.0

    created_at_unix = raw.get("created_at")
    created_at_iso = None
    if created_at_unix is not None:
        try:
            created_at_iso = datetime.fromtimestamp(int(created_at_unix), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            created_at_iso = None

    return {
        "transaction_id": raw.get("id"),
        "source_id": source.get("id"),
        "source_type": source_type,
        "amount": _to_decimal(raw.get("amount")),
        "currency": raw.get("currency"),
        "debit": _to_decimal(raw.get("debit")),
        "credit": _to_decimal(raw.get("credit")),
        "fees": _to_decimal(source.get("fees")),
        "tax": _to_decimal(source.get("tax")),
        "status": source.get("status") or "settled",
        "utr": raw.get("utr"),
        "mode": raw.get("mode"),
        "created_at": created_at_iso,
    }
