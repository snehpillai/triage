# Billing FAQ

## 1. Billing Cycle

Subscriptions are billed on the same calendar date each month as the original signup date. A customer who signed up on the 15th is billed on the 15th of each subsequent month.

If the signup date falls on the 29th, 30th, or 31st, billing in shorter months defaults to the last day of that month. A customer billed on January 31 will be billed on February 28 (or 29 in a leap year), then March 31, then April 30. The billing date does not permanently shift — it returns to the original date in months where it exists.

Invoices are sent by email within 1 hour of a successful charge. If a customer did not receive an invoice, they should check their spam or promotions folder and confirm the billing email address is correct in account settings. Invoices are also accessible at any time under Settings > Billing > Invoice History.

## 2. Proration Rules

**Mid-cycle upgrades** are charged immediately and prorated to the day. The prorated amount is calculated as:

`(Days remaining in billing period ÷ Total days in billing period) × (New plan price − Old plan price)`

Example: A customer on a $20/month plan upgrades to a $50/month plan with 15 days remaining in a 30-day billing period. The prorated charge is (15 ÷ 30) × ($50 − $20) = **$15.00**, charged immediately. The next full billing cycle charges $50.00.

**Mid-cycle downgrades** do not generate an immediate charge or credit. The downgrade is queued and takes effect at the start of the next billing cycle. The customer retains full access to higher-tier features until their current cycle ends.

No partial credit is issued for the unused portion of a higher-tier billing period when downgrading.

## 3. Upgrade and Downgrade Behavior

**Upgrading**
Access to new features and increased limits is granted immediately upon successful payment of the prorated charge. If the prorated charge fails, the upgrade does not take effect and the account remains on the current plan. The customer will be notified of the failed charge and can retry from the billing page.

**Downgrading**
The downgrade is queued for the next billing cycle. The customer receives an email confirming the scheduled downgrade date and the new monthly charge amount. If the customer wants to reverse the downgrade before the next billing cycle, they can cancel it through Settings > Billing > Plan Changes at no charge.

**Cancellation**
Cancellation takes effect at the end of the current billing cycle. Access is maintained until that date. No prorated refund is issued for unused time in the current cycle unless the account was charged in error (see Section 6).

## 4. Failed Payment Retry Schedule

When a payment attempt fails, the following sequence applies automatically:

- **Day 0 (original charge date):** Charge attempt fails. The customer is notified by email with instructions to update their payment method. Account access is not affected at this point.
- **Day 3:** First automatic retry. If successful, the billing cycle resumes normally and no further action is needed. If it fails again, a second notification is sent informing the customer of the next retry date.
- **Day 7:** Second automatic retry. If successful, billing cycle resumes. If it fails, a final warning email is sent stating that the account will be suspended the following day.
- **Day 8:** Account is suspended. The customer loses access to the service. Account data is retained for 30 days from the suspension date.
- **Day 38:** If no successful payment has been received within 30 days of the suspension date, the account is permanently closed and data deletion begins per the data retention policy.

At any point in this cycle, the customer can update their payment method from Settings > Billing > Payment Methods. Updating the payment method triggers an immediate retry — the customer does not need to wait for the next scheduled retry date.

## 5. Payment Method Update Process

To update a payment method:

1. Log in and navigate to Settings > Billing > Payment Methods.
2. Add a new card or bank account, or update the existing method.
3. The system automatically retries any outstanding failed charge within 1 hour of the update. No additional action is required from the customer.

If the account has already been suspended, the self-serve payment update page is still accessible from the login screen under "Restore Access." Submitting a valid payment from that page triggers an immediate charge attempt. If successful, access is restored within 5 minutes.

If the account has passed the 30-day suspension window and been permanently closed, self-serve reactivation is not possible. The customer must contact support. Data restoration after permanent closure is not guaranteed and is handled case by case.

If a customer wants to update their payment method outside of a failure cycle (no outstanding balance), the change takes effect at the next billing cycle. There is no charge for adding or updating payment information.

## 6. Dispute and Chargeback Process

**Internal dispute**
If a customer believes they were charged incorrectly — wrong amount, unexpected charge, duplicate charge — they should contact support with the charge date and the amount in question. Support will review the billing history and issue a correction within 5 business days if the charge was in error. Corrected charges are refunded to the original payment method under the standard refund timelines (see Refund Policy, Section 5).

Customers should use the internal dispute process before filing a chargeback. Internal disputes are resolved faster and do not affect account standing.

**Chargeback filed with bank or card provider**
If a customer files a chargeback with their financial institution, the account is automatically suspended pending the outcome of the chargeback dispute. This is an automated response and cannot be manually overridden while the chargeback is active with the payment provider. The review process between the company and the payment provider typically takes 30–45 business days.

A customer who files a chargeback while an internal dispute is open will have the internal dispute closed immediately. The two processes cannot run concurrently.

If the chargeback is decided in the customer's favor, the account remains closed. If decided in the company's favor, the suspension remains in effect and the customer must contact support directly to discuss account status. Reinstatement after a chargeback decision in the company's favor is not automatic and is handled case by case.
