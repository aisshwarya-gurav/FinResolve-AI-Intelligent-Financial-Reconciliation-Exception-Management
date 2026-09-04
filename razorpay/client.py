"""
Razorpay Adapter — reads credentials from environment, supports mock
mode without credentials, fetches and normalizes transactions.

Auth: Razorpay's API uses HTTP Basic Auth with (key_id, key_secret) —
confirmed from their own docs' curl examples
(`curl -u [YOUR_KEY_ID]:[YOUR_KEY_SECRET] ...`). We use requests' `auth=`
tuple for this, which handles the encoding — credentials are never
manually formatted into a string that could accidentally end up in a
log line.

Credential safety: no code path in this file logs self.key_id or
self.key_secret, under any log level, in any exception message. Errors
report the HTTP status code and error type, never the request's auth
tuple or headers.
"""

import os
import logging
import requests

from .fixtures import get_mock_transactions
from .normalizer import normalize_transaction, VALID_SOURCE_TYPES

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.razorpay.com/v1"
DEFAULT_TIMEOUT_SECONDS = 10


class RazorpayAPIError(Exception):
    """Raised for any failure calling the real Razorpay API — bad
    status code, network failure, or malformed response. Never includes
    credentials in its message."""
    pass


class RazorpayTimeoutError(RazorpayAPIError):
    """Raised specifically when the request times out."""
    pass


class RazorpayAdapter:
    def __init__(self, key_id: str = None, key_secret: str = None, mock_mode: bool = None,
                 base_url: str = DEFAULT_BASE_URL, timeout: int = DEFAULT_TIMEOUT_SECONDS):
        self.key_id = key_id or os.environ.get("RAZORPAY_KEY_ID")
        self.key_secret = key_secret or os.environ.get("RAZORPAY_KEY_SECRET")
        self.base_url = base_url
        self.timeout = timeout

        if mock_mode is None:
            # No explicit choice made -> mock mode automatically when
            # credentials aren't available, so this adapter is usable
            # (e.g. in tests, in this sandbox) without ever requiring
            # real Razorpay access.
            self.mock_mode = not (self.key_id and self.key_secret)
        else:
            self.mock_mode = mock_mode

        if not self.mock_mode and not (self.key_id and self.key_secret):
            raise RazorpayAPIError(
                "mock_mode=False but RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET are not set."
            )

        # Log ONLY whether credentials are present, never their values.
        logger.info(
            "RazorpayAdapter initialized (mock_mode=%s, credentials_present=%s)",
            self.mock_mode, bool(self.key_id and self.key_secret),
        )

    def fetch_transactions(self, source_type: str, account_number: str = None, count: int = 10) -> list:
        """Fetches and normalizes up to `count` transactions of the
        given source_type. In mock mode, returns fixture data. In real
        mode, calls the Razorpay Transactions API and filters client-side
        by source type (the fetch-all endpoint doesn't document a
        source-entity filter param)."""

        if source_type not in VALID_SOURCE_TYPES:
            raise ValueError(f"Unknown source_type '{source_type}'. Use 'payout' or 'bank_transfer'.")

        if self.mock_mode:
            raw_items = get_mock_transactions(source_type, count)
        else:
            raw_items = self._fetch_real(source_type, account_number, count)

        return [normalize_transaction(item, source_type) for item in raw_items]

    def _fetch_real(self, source_type: str, account_number: str, count: int) -> list:
        params = {"count": count}
        if account_number:
            params["account_number"] = account_number

        try:
            response = requests.get(
                f"{self.base_url}/transactions",
                auth=(self.key_id, self.key_secret),
                params=params,
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout as e:
            logger.error("Razorpay API request timed out after %ss", self.timeout)
            raise RazorpayTimeoutError(f"Request to Razorpay timed out after {self.timeout}s") from e
        except requests.exceptions.RequestException as e:
            logger.error("Razorpay API request failed: %s", type(e).__name__)
            raise RazorpayAPIError(f"Razorpay API request failed: {type(e).__name__}") from e

        if response.status_code >= 400:
            logger.error("Razorpay API returned HTTP %s", response.status_code)
            raise RazorpayAPIError(f"Razorpay API returned HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as e:
            raise RazorpayAPIError("Razorpay API returned a non-JSON response") from e

        items = data.get("items", [])

        # Client-side filter: real credit-side entity name is unconfirmed
        # (see fixtures.py docstring), so 'bank_transfer' is matched as
        # "anything whose source entity is not 'payout'" rather than an
        # exact string match that might not correspond to reality.
        if source_type == "payout":
            return [i for i in items if (i.get("source") or {}).get("entity") == "payout"]
        else:
            return [i for i in items if (i.get("source") or {}).get("entity") != "payout"]
