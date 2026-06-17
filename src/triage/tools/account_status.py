from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel


class AccountInfo(BaseModel):
    customer_id: str
    status: Literal["active", "suspended", "payment_failed", "not_found"]
    plan_tier: str  # free | basic | pro | enterprise
    signup_date: str  # ISO 8601 date
    last_payment_date: str | None = None
    next_billing_date: str | None = None
    payment_failures_in_last_90_days: int


# Stub data. Extend this dict to add more test accounts.
# Mix of plan tiers and statuses covering the scenarios in billing_faq.md
# and account_faq.md.
_ACCOUNTS: dict[str, AccountInfo] = {
    "CUST-2001": AccountInfo(
        customer_id="CUST-2001",
        status="active",
        plan_tier="pro",
        signup_date="2023-04-10",
        last_payment_date="2026-06-10",
        next_billing_date="2026-07-10",
        payment_failures_in_last_90_days=0,
    ),
    "CUST-2002": AccountInfo(
        customer_id="CUST-2002",
        status="suspended",
        plan_tier="basic",
        signup_date="2024-01-15",
        last_payment_date="2026-03-15",
        next_billing_date=None,
        payment_failures_in_last_90_days=3,
    ),
    "CUST-2003": AccountInfo(
        customer_id="CUST-2003",
        status="active",
        plan_tier="enterprise",
        signup_date="2022-09-01",
        last_payment_date="2026-06-01",
        next_billing_date="2026-07-01",
        payment_failures_in_last_90_days=0,
    ),
    "CUST-2004": AccountInfo(
        customer_id="CUST-2004",
        status="payment_failed",
        plan_tier="basic",
        signup_date="2025-03-20",
        last_payment_date="2026-05-20",
        next_billing_date="2026-06-20",
        payment_failures_in_last_90_days=1,
    ),
    "CUST-2005": AccountInfo(
        customer_id="CUST-2005",
        status="active",
        plan_tier="free",
        signup_date="2025-11-05",
        last_payment_date=None,
        next_billing_date=None,
        payment_failures_in_last_90_days=0,
    ),
    "CUST-2006": AccountInfo(
        customer_id="CUST-2006",
        status="suspended",
        plan_tier="pro",
        signup_date="2023-07-22",
        last_payment_date="2026-04-22",
        next_billing_date=None,
        payment_failures_in_last_90_days=2,
    ),
    "CUST-2007": AccountInfo(
        customer_id="CUST-2007",
        status="active",
        plan_tier="basic",
        signup_date="2024-06-30",
        last_payment_date="2026-06-01",
        next_billing_date="2026-07-01",
        payment_failures_in_last_90_days=0,
    ),
    "CUST-2008": AccountInfo(
        customer_id="CUST-2008",
        status="payment_failed",
        plan_tier="pro",
        signup_date="2023-12-01",
        last_payment_date="2026-05-01",
        next_billing_date="2026-06-01",
        payment_failures_in_last_90_days=2,
    ),
    "CUST-2009": AccountInfo(
        customer_id="CUST-2009",
        status="active",
        plan_tier="enterprise",
        signup_date="2021-05-15",
        last_payment_date="2026-06-15",
        next_billing_date="2026-07-15",
        payment_failures_in_last_90_days=0,
    ),
    "CUST-2010": AccountInfo(
        customer_id="CUST-2010",
        status="suspended",
        plan_tier="basic",
        signup_date="2024-08-10",
        last_payment_date="2026-02-10",
        next_billing_date=None,
        payment_failures_in_last_90_days=4,
    ),
}

_NOT_FOUND = AccountInfo(
    customer_id="",
    status="not_found",
    plan_tier="",
    signup_date="",
    payment_failures_in_last_90_days=0,
)


@tool
def account_status(customer_id: str) -> AccountInfo:
    """Look up a customer account by its ID. Returns account status, plan tier,
    billing dates, and payment failure count. Returns status='not_found' for
    unknown customer IDs - never raises an exception for missing accounts.
    """
    result = _ACCOUNTS.get(customer_id)
    if result is None:
        return _NOT_FOUND.model_copy(update={"customer_id": customer_id})
    return result
