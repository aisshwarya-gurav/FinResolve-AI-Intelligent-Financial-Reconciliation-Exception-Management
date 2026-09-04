"""
Reconciliation UI — common data-ingestion layer.
"""
import io
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "finrca_data"))

from matcher import match_records  # noqa: E402
from three_way_matcher import match_three_way  # noqa: E402
from case_management.ingestion import _ingest_record  # noqa: E402


class DataNormalizationError(ValueError):
    """Raised when an uploaded dataset cannot be normalized to the expected schema."""


PAYMENT_ALIASES = {
    "payment_id": ["payment_id", "payment id", "id", "txn_id", "transaction_id"],
    "vendor_id": ["vendor_id", "vendor id", "supplier_id", "counterparty_id"],
    "payment_date": ["payment_date", "payment date", "date", "txn_date", "transaction_date"],
    "payment_currency": ["payment_currency", "currency", "ccy"],
    "payment_amount": ["payment_amount", "amount", "amount_inr", "total_amount", "gross_amount"],
    "payment_status": ["payment_status", "status"],
    "settlement_status": ["settlement_status", "settlement status"],
    "reference_number": ["reference_number", "reference", "ref_no", "payment_reference", "utr"],
}

BANK_ALIASES = {
    "bank_transaction_id": ["bank_transaction_id", "bank transaction id", "transaction_id", "id"],
    "payment_reference": ["payment_reference", "reference_number", "reference", "utr", "ref_no"],
    "amount": ["amount", "bank_amount", "transaction_amount"],
    "currency": ["currency", "ccy"],
    "transaction_date": ["transaction_date", "date", "posted_date", "value_date"],
    "counterparty_token": ["counterparty_token", "counterparty", "counterparty_id", "account_id"],
}

INVOICE_ALIASES = {
    "invoice_id": ["invoice_id", "invoice id", "id"],
    "vendor_id": ["vendor_id", "vendor id", "supplier_id"],
    "invoice_total": ["invoice_total", "total", "amount", "invoice_amount", "total_amount"],
    "invoice_date": ["invoice_date", "date", "invoice date"],
    "due_date": ["due_date", "due date"],
    "status": ["status", "invoice_status"],
}

ALLOCATION_ALIASES = {
    "payment_id": ["payment_id", "payment id"],
    "invoice_id": ["invoice_id", "invoice id"],
    "allocated_amount": ["allocated_amount", "amount", "allocation_amount"],
}


def _norm(name: str) -> str:
    """Normalize a column name for case/whitespace-insensitive matching."""
    return " ".join(str(name).strip().lower().split())


def _build_alias_index(aliases: dict) -> dict:
    """Build a lookup from normalized alias -> canonical column name."""
    index = {}
    for canonical, names in aliases.items():
        for name in names:
            index[_norm(name)] = canonical
    return index


def normalize_dataframe(df: pd.DataFrame, aliases: dict, label: str) -> pd.DataFrame:
    """Normalize a DataFrame's columns to the canonical matcher schema."""
    if df is None or df.empty:
        raise DataNormalizationError(f"{label} is empty.")

    alias_index = _build_alias_index(aliases)
    column_map = {}
    seen_canonical = set()
    for col in df.columns:
        normalized = _norm(col)
        if normalized in alias_index:
            canonical = alias_index[normalized]
            # If multiple source columns map to the same canonical name,
            # keep only the first one to avoid duplicate columns.
            if canonical not in seen_canonical:
                column_map[col] = canonical
                seen_canonical.add(canonical)

    if not column_map:
        expected = ", ".join(sorted(aliases.keys()))
        raise DataNormalizationError(
            f"Could not match any columns in {label}. Expected columns like: {expected}"
        )

    result = df.rename(columns=column_map)

    missing = [col for col in aliases if col not in result.columns]
    if missing:
        raise DataNormalizationError(
            f"{label} is missing required column(s): {', '.join(missing)}. "
            f"Found columns: {', '.join(str(c) for c in df.columns)}"
        )

    return result


def load_uploaded_csv(uploaded_file, aliases: dict, label: str) -> pd.DataFrame:
    """Load and normalize an uploaded CSV file."""
    if uploaded_file is None:
        raise DataNormalizationError(f"Please upload a {label} file.")

    filename = (uploaded_file.name or "").lower()
    if filename and not filename.endswith(".csv"):
        raise DataNormalizationError(
            f"Invalid file type for {label}: '{uploaded_file.name}'. Please upload a .csv file."
        )

    try:
        # Read the raw bytes from the uploaded file (works with Streamlit's
        # UploadedFile and any file-like object that has getvalue()/read()).
        if hasattr(uploaded_file, "getvalue"):
            raw = uploaded_file.getvalue()
        else:
            raw = uploaded_file.read()

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig", errors="replace")

        df = pd.read_csv(io.StringIO(raw))
    except DataNormalizationError:
        raise
    except Exception as exc:
        raise DataNormalizationError(f"Could not parse {label} as CSV: {exc}") from exc

    return normalize_dataframe(df, aliases, label)


def run_two_way_upload(payments_file, bank_file) -> dict:
    """Run the existing two-way matcher on uploaded CSVs."""
    payments_df = load_uploaded_csv(payments_file, PAYMENT_ALIASES, "Payments CSV")
    bank_df = load_uploaded_csv(bank_file, BANK_ALIASES, "Bank CSV")

    return match_records(payments_df, bank_df)


def run_three_way_upload(invoices_file, allocations_file, payments_file, bank_file) -> dict:
    """Run the existing three-way matcher on uploaded CSVs."""
    invoices_df = load_uploaded_csv(invoices_file, INVOICE_ALIASES, "Invoices CSV")
    allocations_df = load_uploaded_csv(allocations_file, ALLOCATION_ALIASES, "Payment Allocations CSV")
    payments_df = load_uploaded_csv(payments_file, PAYMENT_ALIASES, "Payments CSV")
    bank_df = load_uploaded_csv(bank_file, BANK_ALIASES, "Bank CSV")

    return match_three_way(invoices_df, allocations_df, payments_df, bank_df)


def ingest_two_way_results(store, results: dict, actor: str = "system") -> list:
    """Ingest two-way match results into the case store."""
    created = []
    for bucket in ("amount_mismatch", "payment_no_bank", "bank_no_payment"):
        for record in results.get(bucket, []):
            _ingest_record(store, record, record.get("issue", bucket), "finrca_two_way")
            created.append(record)
    return created


def ingest_three_way_results(store, results: dict, actor: str = "system") -> list:
    """Ingest three-way match results into the case store."""
    created = []
    for bucket in ("partial_payment", "overpaid_duplicate", "payment_without_invoice", "chain_bank_exception"):
        for record in results.get(bucket, []):
            _ingest_record(store, record, record.get("issue", bucket), "finrca_three_way")
            created.append(record)
    return created