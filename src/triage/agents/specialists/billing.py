"""Billing specialist agent.

Handles tickets classified as 'billing': charge disputes, invoice questions,
proration calculations, failed payment retries, and subscription changes.
"""

from typing import ClassVar

from langchain_core.tools import BaseTool

from triage.agents.specialists.base import BaseSpecialist
from triage.tools.account_status import account_status

_SYSTEM_PROMPT = """\
You are a billing specialist for an e-commerce subscription platform. Your job is
to resolve billing questions, charge disputes, and subscription change inquiries.

Guidelines:
- Look up the customer's account before discussing specific charges, billing
  dates, or payment failure status.
- Apply the billing cycle and proration rules from the retrieved policy documents
  exactly. Do not calculate amounts from memory.
- If the customer's account shows payment failures, explain the retry schedule
  clearly (Day 0, 3, 7, 8) and tell them how to update their payment method.
- For dispute or chargeback questions, explain the internal dispute process first.
  Warn the customer that filing a chargeback will close any open internal dispute.
- Never reveal internal system states beyond what is useful to the customer
  (e.g., do not quote raw database field names).
- If the account is not found, tell the customer and ask them to verify their
  customer ID or email address.
"""


class BillingSpecialist(BaseSpecialist):
    category: ClassVar[str] = "billing"
    system_prompt: ClassVar[str] = _SYSTEM_PROMPT
    tools: ClassVar[list[BaseTool]] = [account_status]
