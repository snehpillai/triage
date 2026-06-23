"""Generate tests/eval/datasets/tickets_500.json.

Calls Claude (claude-sonnet-4-6) to produce 500 labelled ticket fixtures
in 20 batches of 25. Every ticket is grounded in the actual policy documents
under data/knowledge_base/.

Usage (from repo root):
    python scripts/generate_eval_dataset.py

The script is idempotent -- re-running overwrites the output file.
Set ANTHROPIC_API_KEY in your environment or .env file before running.
"""

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import after path setup so triage.config can load .env
import anthropic
from loguru import logger

from triage.config import settings

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

_REPO = Path(__file__).parent.parent
_KB = _REPO / "data" / "knowledge_base"
_OUT = _REPO / "tests" / "eval" / "datasets" / "tickets_500.json"

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 16384
_MAX_RETRIES = 3
_INTER_BATCH_DELAY = 1.0  # seconds between calls

VALID_INTENTS = {"refund", "technical", "billing", "account", "ambiguous"}
VALID_CATEGORIES = {"happy_path", "edge_case", "ambiguous", "pii_in_input", "hostile_tone"}
REQUIRED_FIELDS = {
    "id",
    "content",
    "expected_intent",
    "category",
    "resolution_criteria",
    "should_escalate",
}

# ---------------------------------------------------------------------------
# Batch specification
#
# Each entry: (batch_num, id_start, primary_intent, groups)
# Each group: (category, count, description, should_escalate)
#
# Groups in the same batch can have different categories -- used for
# mixed batches (e.g. 20 hostile_tone + 5 pii_in_input).
# ---------------------------------------------------------------------------

BATCH_SPECS = [
    # ------------------------------------------------------------------ REFUND (150 total)
    (
        1,
        1,
        "refund",
        [
            (
                "happy_path",
                25,
                "Full refund: item arrived visibly damaged at delivery (photo documentation required). "
                "Include varied scenarios: cracked screens, crushed packaging, broken parts, liquid damage "
                "from shipping. All within the 30-day window, customer has or will supply photos. "
                "Include the order number in each content field (ORD-1001 through ORD-1025 range).",
                False,
            ),
        ],
    ),
    (
        2,
        26,
        "refund",
        [
            (
                "happy_path",
                25,
                "Full refund scenarios other than damage: (a) wrong item shipped -- model, color, or size "
                "does not match the order confirmation; (b) item not delivered and tracking shows no movement "
                "for 5+ consecutive business days; (c) carrier confirmed item lost in transit. "
                "Also include 5 partial refund cases: missing listed accessory (refund = retail value of "
                "missing part) or cosmetic damage only (10-25% of item price).",
                False,
            ),
        ],
    ),
    (
        3,
        51,
        "refund",
        [
            (
                "edge_case",
                25,
                "Missing-information refund requests: customer wants a refund but is missing one or more "
                "required details. Vary the missing element: no order number provided, damage claim with no "
                "photo offered, multi-item order but no indication which item, vague description ('it broke'), "
                "no indication of whether the 30-day window has been met, customer asking if they need to send "
                "the item back before even requesting the RMA.",
                False,
            ),
        ],
    ),
    (
        4,
        76,
        "refund",
        [
            (
                "edge_case",
                25,
                "Borderline eligibility edge cases. Include all of the following: "
                "(a) request exactly on day 30 of the window; "
                "(b) final-sale item -- customer claiming it arrived defective (eligible) vs. customer changing "
                "their mind (not eligible); "
                "(c) clearance item -- customer expecting cash refund (only store credit unless defective); "
                "(d) digital goods -- software already downloaded or activated (not eligible); "
                "(e) customer-caused damage -- dropped, liquid exposure, unauthorized disassembly; "
                "(f) non-returnable category -- personal care item used after opening, perishable; "
                "(g) change-of-mind for a working, undamaged product that is not final-sale.",
                False,
            ),
        ],
    ),
    (
        5,
        101,
        "refund",
        [
            (
                "hostile_tone",
                20,
                "Angry, aggressive, or threatening refund demands. Vary tone and scenario: "
                "threatening immediate chargeback if not refunded today, all-caps shouting, profanity-adjacent "
                "frustration ('this is absolute garbage'), accusations of fraud or scam, threatening social "
                "media exposure or BBB complaint, repeating a prior denied request with escalating anger, "
                "demanding to speak to a manager and refusing to interact with 'bots'. "
                "Keep the underlying refund scenario legally valid in some cases and invalid (denied) in others "
                "so the criteria can test both outcomes under hostile conditions.",
                False,
            ),
            (
                "pii_in_input",
                5,
                "Refund requests where the customer has included PII in the body believing support needs it "
                "for identity verification: embed a realistic-looking but fake SSN (format NNN-NN-NNNN), "
                "a 16-digit credit card number, or a bank account/routing number pair. The underlying refund "
                "request should be a standard happy-path scenario. The category field must be pii_in_input.",
                False,
            ),
        ],
    ),
    (
        6,
        126,
        "refund",
        [
            (
                "edge_case",
                20,
                "Additional refund policy edge cases: "
                "(a) sale item -- customer asking if the refund is calculated at the sale price or the original "
                "price (answer: amount the customer paid); "
                "(b) partial refund for late delivery -- arrived 6-8 business days after estimated date "
                "(partial; not late enough for full refund); "
                "(c) customer who had a refund denied and now wants to file an internal dispute -- what is the "
                "process, timeline (72 business hours for senior review), and what happens if denied again; "
                "(d) customer who filed a chargeback while an internal dispute was open -- asks why the "
                "internal dispute was closed.",
                False,
            ),
            (
                "pii_in_input",
                5,
                "Refund requests where the customer includes SSN, a full 16-digit card number, or both email "
                "and password ('for verification') in the ticket body. Underlying refund scenarios should be "
                "varied: one happy path, one edge case, one missing info.",
                False,
            ),
        ],
    ),
    # ------------------------------------------------------------------ TECHNICAL (125 total)
    (
        7,
        151,
        "technical",
        [
            (
                "happy_path",
                25,
                "Known error codes with clear resolution paths. Cover ERR-401, ERR-403, ERR-404. "
                "ERR-401: session expired -- log out, clear cookies/cache, log in; if persists after fresh "
                "login, reset password. "
                "ERR-403: check subscription tier for the feature; check for admin-applied restriction; "
                "escalate to Tier 2 with account ID and specific resource URL if neither applies. "
                "ERR-404: verify URL/item ID; check activity log for deletion event; if deleted within 7 days "
                "escalate to engineering for possible database restore. "
                "Vary customer context: new user who doesn't know error codes, developer hitting API errors, "
                "returning customer confused by a sudden change.",
                False,
            ),
        ],
    ),
    (
        8,
        176,
        "technical",
        [
            (
                "happy_path",
                25,
                "More known issue resolutions. Cover ERR-429 and ERR-503 and account lockout. "
                "ERR-429: rate limit resets automatically after 60 seconds; no manual action; if Standard plan "
                "consistently hits 100 req/min, route to sales for Enterprise evaluation. "
                "ERR-503: check status page first; read posted incident update verbatim; no ETAs or causes "
                "beyond what is published; do not offer proactive callbacks unless tooling supports it; "
                "log customer report to incident ticket. "
                "Account lockout (5 failed attempts in 10 min): auto-lifts in 15 minutes; password reset "
                "unlocks immediately. "
                "Vary scenarios: customer in the middle of a deadline hitting 503, developer at rate limit, "
                "customer locked out repeatedly because they forgot which email they used.",
                False,
            ),
        ],
    ),
    (
        9,
        201,
        "technical",
        [
            (
                "edge_case",
                25,
                "Connectivity and browser troubleshooting edge cases requiring the 5-step protocol. "
                "Scenarios: issue only in normal browser mode but not incognito (browser extension likely); "
                "issue only on corporate WiFi not on mobile hotspot (network-level, outside app control); "
                "VPN interfering with authentication tokens; ad-blocker or security extension blocking API "
                "requests; stale cached data causing intermittent failures; multiple failed steps before "
                "escalation is warranted. "
                "Some cases resolve at step 2 or 3; some require the full 5 steps and then escalation with "
                "browser/OS/error details. Vary customer technical sophistication.",
                False,
            ),
        ],
    ),
    (
        10,
        226,
        "technical",
        [
            (
                "edge_case",
                20,
                "Unusual technical issues not matching standard error codes. "
                "(a) Unsupported configuration: Internet Explorer, Windows 7, macOS Big Sur, iOS 14 -- "
                "ask to reproduce on supported browser before investigating. "
                "(b) Customer reports a deleted resource within the 7-day engineering restore window -- "
                "must escalate with account ID and specific resource ID. "
                "(c) Security-triggered account lock (unusual activity message, not failed-attempt lock) -- "
                "requires identity verification, cannot be resolved by waiting. "
                "(d) Customer on Opera or non-standard Chromium browser -- generally functions but not "
                "officially tested; ask to reproduce on supported browser. "
                "Make resolution_criteria specific about what distinguishes these from standard cases.",
                False,
            ),
            (
                "ambiguous",
                5,
                "Tickets that mix technical and account concerns in a way that makes routing genuinely "
                "unclear. Examples: 'I can't log in AND my billing page is missing' (technical + billing + "
                "account), 'my account was locked and now I have a charge I don't recognize' (account + "
                "billing), 'I'm getting ERR-403 on the invoice download page' (technical + billing). "
                "Set expected_intent to 'ambiguous', category to 'ambiguous', should_escalate to true. "
                "Resolution criteria should describe what correct escalation looks like.",
                True,
            ),
        ],
    ),
    (
        11,
        251,
        "technical",
        [
            (
                "hostile_tone",
                15,
                "Hostile or threatening technical support requests. Scenarios: customer furious the service "
                "is down during a paid event, threatening to cancel and demand a full refund, threatening to "
                "go to a competitor, demanding to be connected to an engineer immediately, accusing support of "
                "not knowing the product. Some requests should have a genuine technical issue (ERR-503 during "
                "outage, ERR-429 due to product bug) that the agent should still address despite the hostility. "
                "Resolution criteria must address both the tone management AND the correct technical response.",
                False,
            ),
            (
                "pii_in_input",
                10,
                "Technical support tickets where the customer has shared sensitive credentials or identifiers, "
                "believing support needs them: username and password in plain text, API key or OAuth token, "
                "full IP address and MAC address, device serial number and IMEI. "
                "The underlying technical issue should be varied (ERR-401, connectivity, locked account). "
                "Resolution criteria must state that agent must NOT use or acknowledge the shared credentials "
                "and should advise the customer to rotate any shared secrets immediately.",
                False,
            ),
        ],
    ),
    # ------------------------------------------------------------------ BILLING (125 total)
    (
        12,
        276,
        "billing",
        [
            (
                "happy_path",
                25,
                "Standard billing inquiries with clear policy answers. "
                "(a) Invoice not received: check spam, verify billing email in settings, access via "
                "Settings > Billing > Invoice History. "
                "(b) Billing cycle date: billed same calendar day as signup; months with fewer days default "
                "to last day of month but return to original date when possible. "
                "(c) Payment method update: Settings > Billing > Payment Methods, triggers immediate retry of "
                "any outstanding failed charge within 1 hour. "
                "(d) Upgrade/downgrade cancellation: can cancel a queued downgrade via "
                "Settings > Billing > Plan Changes before it takes effect.",
                False,
            ),
        ],
    ),
    (
        13,
        301,
        "billing",
        [
            (
                "happy_path",
                25,
                "Subscription lifecycle questions with clear answers. "
                "(a) Upgrade: access to new features is immediate upon successful prorated charge; "
                "if charge fails, upgrade does not take effect. "
                "(b) Downgrade: queued for next cycle, no immediate credit, access to higher-tier features "
                "until end of cycle. "
                "(c) Cancellation: access maintained until end of billing cycle, no prorated refund for "
                "unused time. "
                "(d) Failed payment sequence: Day 0 (notification), Day 3 (first retry), Day 7 (second retry), "
                "Day 8 (suspension), Day 38 (permanent closure). "
                "(e) Self-serve reactivation from login page after payment failure suspension. "
                "Include calculation questions: customer asks 'how much will I be charged if I upgrade from "
                "$20 to $50 with 15 days left in a 30-day cycle?' (answer: $15.00 using the proration formula).",
                False,
            ),
        ],
    ),
    (
        14,
        326,
        "billing",
        [
            (
                "edge_case",
                25,
                "Billing edge cases requiring careful policy application. "
                "(a) Customer signed up on January 31, asks why they were billed on February 28 -- correct "
                "behavior per policy (shorter months default to last day, returns to 31 in March). "
                "(b) Mid-cycle upgrade proration: customer confused about the prorated charge amount; "
                "include a scenario with a specific calculation. "
                "(c) Account suspended on Day 8 -- customer wants to reactivate but is within the 30-day window "
                "(self-serve via 'Restore Access' on login page). "
                "(d) Customer past the 30-day suspension window (Day 38+) -- self-serve not available, must "
                "contact support; data retention question. "
                "(e) Downgrade confusion: customer expected immediate credit but did not receive one. "
                "(f) Customer who updated payment method and wants to know when the retry will happen (within 1 hour).",
                False,
            ),
        ],
    ),
    (
        15,
        351,
        "billing",
        [
            (
                "edge_case",
                20,
                "Billing disputes and chargeback edge cases. "
                "(a) Incorrect charge amount: charged $50 but expected $35; customer asks what to do. "
                "(b) Duplicate charge: charged twice in the same billing period. "
                "(c) Unexpected subscription renewal: customer thought they had cancelled but was still charged. "
                "(d) Charged for an upgrade that failed: customer says the upgrade did not go through but "
                "their card was charged. "
                "Resolution criteria should include: contact support with charge date and amount, 5-business-day "
                "correction window, corrected charges refunded per standard refund timelines. "
                "Explicitly note that internal dispute is faster and safer than a chargeback.",
                False,
            ),
            (
                "ambiguous",
                5,
                "Tickets that genuinely blur the line between billing and account or billing and technical. "
                "Examples: 'I can't access my account and my card was just charged' (account suspension + "
                "billing), 'the billing page shows an error when I try to update my payment method' "
                "(billing + technical), 'I filed a chargeback and now I can't log in' (billing + account "
                "suspension interaction). "
                "Set expected_intent to 'ambiguous', category to 'ambiguous', should_escalate to true.",
                True,
            ),
        ],
    ),
    (
        16,
        376,
        "billing",
        [
            (
                "hostile_tone",
                10,
                "Hostile billing interactions. Scenarios: customer threatening to file a chargeback immediately "
                "if not refunded within the hour, accusing the company of unauthorized charges and fraud, "
                "insisting they cancelled months ago and demanding all charges reversed, angrily disputing a "
                "proration amount they calculate differently. "
                "Resolution criteria must address both tone management (no matching aggression, no "
                "unconditional promises) AND the correct billing policy outcome for the scenario.",
                False,
            ),
            (
                "pii_in_input",
                5,
                "Billing tickets where the customer has included sensitive payment data in the body: full "
                "16-digit credit card number with expiry and CVV, bank account and routing number pair, "
                "or PayPal login email and password. The underlying billing inquiry should be a real one "
                "(update payment method, dispute a charge, question about proration). "
                "Resolution criteria must state agent must NOT store, repeat, or acknowledge the sensitive "
                "payment data and should advise the customer to update their payment information only through "
                "the secure Settings > Billing > Payment Methods page.",
                False,
            ),
            (
                "edge_case",
                10,
                "Billing edge cases involving the chargeback process and its consequences. "
                "(a) Customer asking about the difference between an internal dispute and a chargeback -- "
                "which is faster, what are the risks to account standing. "
                "(b) Account suspended because a chargeback was filed (automated, cannot be overridden while "
                "active, 30-45 business day resolution). "
                "(c) Internal dispute closed because customer filed a chargeback while it was open -- customer "
                "asking why. "
                "(d) Chargeback decided in company's favor -- account still suspended, not automatic "
                "reinstatement, must contact support.",
                False,
            ),
        ],
    ),
    # ------------------------------------------------------------------ ACCOUNT (100 total)
    (
        17,
        401,
        "account",
        [
            (
                "happy_path",
                25,
                "Standard account operations with clear answers. "
                "(a) Password reset: forgot-password link, 2-minute email delivery, 60-minute link expiry, "
                "all other sessions invalidated after reset. "
                "(b) 2FA setup: TOTP app (compatible apps, QR code scan, 6-digit confirmation) vs SMS "
                "(6-digit code to verified phone). "
                "(c) Which 2FA method is more secure: TOTP recommended because SMS is vulnerable to SIM-swap "
                "attacks. "
                "(d) Backup codes: generated once during setup, displayed once only, 8 single-use codes, "
                "store offline; can regenerate from Settings > Security (invalidates previous set). "
                "(e) Password reset link expired (60 min): customer must request a new one.",
                False,
            ),
        ],
    ),
    (
        18,
        426,
        "account",
        [
            (
                "edge_case",
                25,
                "Account access edge cases. "
                "(a) Account locked after 5 failed attempts: auto-lifts in 15 minutes; password reset "
                "unlocks immediately without waiting. "
                "(b) Security-triggered lock (unusual activity): displays distinct message about precautionary "
                "measure; requires identity verification; CANNOT be resolved by waiting 15 minutes -- "
                "this is the critical distinction from failed-attempt lockout. "
                "(c) Customer changed phones, SMS 2FA is active, old number no longer accessible: must do "
                "identity verification; cannot update phone number without being authenticated. "
                "(d) All backup codes are used up or lost and 2FA device is unavailable: identity verification "
                "required, 3-5 business days, government ID + one proof of account ownership. "
                "(e) Backup codes regenerated, old codes stop working: customer confused why old codes fail.",
                False,
            ),
        ],
    ),
    (
        19,
        451,
        "account",
        [
            (
                "edge_case",
                10,
                "Data deletion and privacy requests. "
                "(a) GDPR right to erasure / CCPA right to deletion -- how to submit, 30-day processing "
                "window, what is deleted vs retained (transaction records kept 7 years for tax/compliance, "
                "anonymized analytics retained). "
                "(b) Deletion hold: active financial dispute, legal hold, or fraud investigation blocks "
                "deletion until the hold is released. "
                "(c) Deletion request from a suspended or inaccessible account: written request to privacy "
                "email; 30-day clock starts from identity verification completion, not initial request. "
                "(d) Customer asking if deleting account removes purchase history (yes, except transaction "
                "records retained for compliance).",
                False,
            ),
            (
                "ambiguous",
                15,
                "Tickets that mix account, billing, and technical concerns in a way that makes single-intent "
                "routing unreliable. Include scenarios like: "
                "(a) password reset AND billing dispute in the same message; "
                "(b) data deletion request AND an open refund request; "
                "(c) account suspended due to suspicious activity AND the customer wants their billing "
                "history; "
                "(d) 2FA issue AND an API rate limit error; "
                "(e) GDPR deletion request AND an active chargeback hold blocking deletion; "
                "(f) customer asking three unrelated questions (account, billing, technical) in one ticket. "
                "All 15 tickets: expected_intent='ambiguous', category='ambiguous', should_escalate=true. "
                "Resolution criteria should describe what correct triage of an ambiguous ticket looks like "
                "(acknowledge all issues, escalate for human routing, do not fabricate a single intent).",
                True,
            ),
        ],
    ),
    (
        20,
        476,
        "account",
        [
            (
                "hostile_tone",
                10,
                "Hostile account-related interactions. Scenarios: customer demanding account restoration "
                "immediately and threatening legal action, customer refusing to complete identity verification "
                "and insisting on an alternative shortcut, customer disputing a terms-of-service suspension "
                "angrily and threatening a public complaint, customer demanding GDPR erasure be completed "
                "'within 24 hours or I will contact the data protection authority.' "
                "Resolution criteria must address tone management (remain professional, no exceptions due to "
                "hostility) AND correct policy application (30-day deletion window cannot be expedited, "
                "identity verification cannot be bypassed).",
                False,
            ),
            (
                "pii_in_input",
                5,
                "Account recovery requests where the customer includes government-issued ID details in the "
                "ticket body: partial or full SSN, passport number, date of birth, mother's maiden name, "
                "or driver's license number, believing support needs it to restore access. "
                "The underlying account issue should vary: forgotten password, 2FA lockout, suspicious "
                "activity hold. "
                "Resolution criteria must state agent must NOT acknowledge or store PII shared over support "
                "chat; must direct customer to the identity verification portal or the secure upload channel "
                "specified in the identity verification process.",
                False,
            ),
            (
                "edge_case",
                10,
                "Account edge cases not covered in earlier batches. "
                "(a) Terms-of-service suspension: appeal by replying to notification email; 24-48 hour review; "
                "three outcomes (restored with warning, restored with restrictions, permanently closed). "
                "(b) Data compliance hold: support cannot share details about the nature of the hold; can "
                "only confirm if the hold is active and direct customer to contact support. "
                "(c) Account permanently closed after 30-day data retention window: self-serve not available; "
                "contact support; data restoration not guaranteed, case by case. "
                "(d) Reactivation after chargeback resolution in company's favor: not automatic, must contact "
                "support, handled case by case.",
                False,
            ),
        ],
    ),
]

# Verify batch spec sums to 500
_TOTAL = sum(g[1] for _, _, _, groups in BATCH_SPECS for g in groups)
assert _TOTAL == 500, f"BATCH_SPECS ticket count is {_TOTAL}, expected 500"

# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SCHEMA_BLOCK = """\
## Output format

Return a JSON array with exactly {count} objects. No preamble, no markdown fences, no explanation.
Each object must have these fields:
{{
  "id": "eval-NNN",             // zero-padded 3 digits
  "content": "...",             // realistic customer message, 40-250 words
  "expected_intent": "...",     // one of: refund | technical | billing | account | ambiguous
  "category": "...",            // one of: happy_path | edge_case | ambiguous | pii_in_input | hostile_tone
  "resolution_criteria": "...", // specific, testable criteria (see below)
  "should_escalate": false      // boolean
}}"""

_CRITERIA_BLOCK = """\
## Resolution criteria quality standard

Resolution criteria are what an LLM judge will score responses against.
They must be SPECIFIC and TESTABLE -- a judge can check each clause without
knowing the full policy.

REQUIRED: cite specific policy facts (numbers, timelines, rules).
REQUIRED: include what the response MUST do AND what it must NOT do.
REQUIRED: for edge cases, state the CORRECT ruling (eligible/not eligible).
FORBIDDEN: vague directives like "be helpful", "be accurate", "resolve the issue".
LENGTH: each resolution_criteria should be 40-80 words. Precise, not exhaustive.

GOOD examples:

happy_path refund:
"Must confirm the item qualifies for full refund: arrived visibly damaged within "
"the 30-day return window. Must require photo documentation attached at request "
"submission time (submitting later extends processing). Must explain the RMA "
"process: request submitted through account portal, RMA number issued within "
"1 business day, item shipped within 10 business days of RMA issuance. Must "
"state refund is returned to original payment method. Must NOT offer store "
"credit as the default option (store credit only if customer explicitly requests it)."

edge_case billing (downgrade):
"Must correctly explain that mid-cycle downgrades do NOT generate an immediate "
"credit -- the downgrade is queued for the start of the next billing cycle. Must "
"confirm the customer retains full access to higher-tier features until the cycle "
"ends. Must state no partial credit is issued for the unused portion of the higher "
"tier. Must direct the customer to Settings > Billing > Plan Changes to verify the "
"scheduled downgrade date and to cancel the downgrade if desired."

pii_in_input:
"Must NOT acknowledge, repeat, store, or reference the customer's SSN in the "
"response body. Must process the underlying refund request on its merits. Must "
"advise the customer that SSNs are not needed for refund processing and should "
"not be shared over support channels."

hostile_tone (valid claim, hostile delivery):
"Must de-escalate without matching the customer's tone or profanity. Must not "
"make policy exceptions because of the hostile tone. Must assess refund eligibility "
"based purely on the facts stated: if within 30-day window and damage is documented, "
"full refund applies. Must not preemptively deny the claim because the customer "
"is angry. Must address the chargeback threat: internal dispute is faster and "
"does not affect account standing."

ambiguous:
"Should escalate because the ticket mixes billing and account issues with no "
"clear primary intent. Must NOT force a single classification. Must acknowledge "
"all distinct issues raised. Should route to a human agent who can address "
"multiple intent categories concurrently."
"""


def _load_policies() -> str:
    return "\n\n".join(
        f"## {path.stem.replace('_', ' ').title()}\n{path.read_text()}"
        for path in sorted(_KB.glob("*.md"))
    )


def _build_system_prompt(policies: str) -> str:
    return f"""\
You are generating a labelled evaluation dataset for an AI customer-support triage system.
This system classifies customer tickets into one of four intents (refund, technical, billing,
account), sends each to a specialist agent, quality-checks the response, and may escalate.

{_SCHEMA_BLOCK}

{_CRITERIA_BLOCK}

## Policy documents (ground all resolution_criteria in these)

{policies}
"""


def _build_user_prompt(
    batch_num: int,
    id_start: int,
    primary_intent: str,
    groups: list[tuple],
) -> str:
    total = sum(g[1] for g in groups)

    lines = [
        f"Generate batch {batch_num}/20.",
        f"IDs: eval-{id_start:03d} through eval-{id_start + total - 1:03d}.",
        f"Primary intent for this batch: {primary_intent}",
        "",
        "Exact distribution:",
    ]

    cursor = id_start
    for category, count, desc, should_escalate in groups:
        id_end = cursor + count - 1
        lines.append(
            f"  - {count} tickets, category={category!r}, "
            f"should_escalate={str(should_escalate).lower()}, "
            f"IDs eval-{cursor:03d} to eval-{id_end:03d}"
        )
        lines.append(f"    Context: {desc}")
        cursor += count

    lines += [
        "",
        "Requirements:",
        "- Vary customer writing style: formal email, casual chat, frustrated rant, brief one-liner.",
        "- Vary customer personas: first-time buyer, long-time subscriber, tech-savvy, non-technical.",
        "- For pii_in_input: embed realistic-but-fake PII in content "
        "(SSN format NNN-NN-NNNN, 16-digit card, routing+account numbers).",
        "- For hostile_tone: vary intensity from mild frustration to threatening.",
        "- For ambiguous: make intent genuinely unclear from context alone.",
        "- Resolution criteria must cite specific policy numbers, timelines, and rules.",
        f"- Return exactly {total} objects as a JSON array. No markdown, no preamble.",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# API call and validation
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> list:
    text = text.strip()
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text.strip())
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        raise ValueError(f"Expected list, got {type(result).__name__}")
    except json.JSONDecodeError:
        # Try to extract a JSON array from anywhere in the text
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


def _validate(tickets: list[dict], id_start: int, groups: list[tuple]) -> list[str]:
    """Return a list of validation error strings (empty = ok)."""
    errors: list[str] = []
    expected_total = sum(g[1] for g in groups)

    if len(tickets) != expected_total:
        errors.append(f"Expected {expected_total} tickets, got {len(tickets)}")

    for i, ticket in enumerate(tickets):
        prefix = f"ticket[{i}]"
        missing = REQUIRED_FIELDS - set(ticket.keys())
        if missing:
            errors.append(f"{prefix} missing fields: {missing}")
            continue

        if ticket["expected_intent"] not in VALID_INTENTS:
            errors.append(f"{prefix} invalid expected_intent={ticket['expected_intent']!r}")
        if ticket["category"] not in VALID_CATEGORIES:
            errors.append(f"{prefix} invalid category={ticket['category']!r}")
        if not isinstance(ticket["should_escalate"], bool):
            errors.append(
                f"{prefix} should_escalate must be bool, got {type(ticket['should_escalate']).__name__}"
            )
        if not ticket.get("content", "").strip():
            errors.append(f"{prefix} empty content")
        if not ticket.get("resolution_criteria", "").strip():
            errors.append(f"{prefix} empty resolution_criteria")
        # Warn (not error) on thin criteria
        crit = ticket.get("resolution_criteria", "")
        if len(crit.split()) < 20:
            errors.append(f"{prefix} resolution_criteria too short ({len(crit.split())} words)")

    return errors


def _assign_ids(tickets: list[dict], id_start: int) -> None:
    """Overwrite IDs to guarantee correct sequential numbering."""
    for i, ticket in enumerate(tickets):
        ticket["id"] = f"eval-{id_start + i:03d}"


def _force_should_escalate(tickets: list[dict], groups: list[tuple]) -> None:
    """Enforce should_escalate=true for ambiguous tickets per the spec."""
    cursor = 0
    for category, count, _desc, should_escalate in groups:
        for ticket in tickets[cursor : cursor + count]:
            if category == "ambiguous":
                ticket["should_escalate"] = True
            elif not should_escalate:
                ticket["should_escalate"] = False
        cursor += count


def generate_batch(
    client: anthropic.Anthropic,
    system_prompt: str,
    batch_num: int,
    id_start: int,
    primary_intent: str,
    groups: list[tuple],
) -> list[dict]:
    user_prompt = _build_user_prompt(batch_num, id_start, primary_intent, groups)

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text
        except anthropic.APIError as exc:
            logger.error("Batch {b} attempt {a}: API error: {e}", b=batch_num, a=attempt, e=exc)
            if attempt < _MAX_RETRIES:
                time.sleep(5)
                continue
            raise

        try:
            tickets = _extract_json(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "Batch {b} attempt {a}: JSON parse failed: {e}", b=batch_num, a=attempt, e=exc
            )
            if attempt < _MAX_RETRIES:
                continue
            raise RuntimeError(
                f"Batch {batch_num}: could not parse JSON after {_MAX_RETRIES} attempts"
            ) from exc

        errors = _validate(tickets, id_start, groups)
        if errors:
            logger.warning(
                "Batch {b} attempt {a}: validation issues:\n{e}",
                b=batch_num,
                a=attempt,
                e="\n".join(f"  {e}" for e in errors[:10]),
            )
            # Hard errors (wrong count, missing fields) warrant a retry
            hard_errors = [
                e for e in errors if "Expected" in e or "missing fields" in e or "must be bool" in e
            ]
            if hard_errors and attempt < _MAX_RETRIES:
                continue

        _assign_ids(tickets, id_start)
        _force_should_escalate(tickets, groups)
        return tickets

    raise RuntimeError(f"Batch {batch_num} failed after {_MAX_RETRIES} attempts")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    _OUT.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Loading policy documents from {p}", p=_KB)
    policies = _load_policies()
    system_prompt = _build_system_prompt(policies)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    all_tickets: list[dict] = []

    logger.info("Generating 500 tickets in {n} batches of 25", n=len(BATCH_SPECS))

    for batch_num, id_start, primary_intent, groups in BATCH_SPECS:
        total = sum(g[1] for g in groups)
        id_end = id_start + total - 1
        logger.info(
            "Batch {b:02d}/{total} | {intent} | eval-{s:03d} to eval-{e:03d}",
            b=batch_num,
            total=len(BATCH_SPECS),
            intent=primary_intent,
            s=id_start,
            e=id_end,
        )

        tickets = generate_batch(client, system_prompt, batch_num, id_start, primary_intent, groups)
        all_tickets.extend(tickets)

        logger.info(
            "  -> {n} tickets generated (total so far: {t})",
            n=len(tickets),
            t=len(all_tickets),
        )

        if batch_num < len(BATCH_SPECS):
            time.sleep(_INTER_BATCH_DELAY)

    assert len(all_tickets) == 500, f"Final count is {len(all_tickets)}, expected 500"

    _OUT.write_text(json.dumps(all_tickets, indent=2, ensure_ascii=False) + "\n")
    logger.info("Written {n} tickets to {p}", n=len(all_tickets), p=_OUT)

    # Print summary statistics
    from collections import Counter

    intents = Counter(t["expected_intent"] for t in all_tickets)
    categories = Counter(t["category"] for t in all_tickets)
    escalated = sum(1 for t in all_tickets if t["should_escalate"])

    logger.info("Intent distribution: {d}", d=dict(intents))
    logger.info("Category distribution: {d}", d=dict(categories))
    logger.info("Tickets with should_escalate=true: {n}", n=escalated)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception("Fatal error: {e}", e=exc)
        sys.exit(1)
