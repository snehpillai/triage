# Account FAQ

## 1. Password Reset Flow

To reset a password:

1. Click "Forgot password" on the login page and enter the email address associated with the account.
2. A reset link is sent to that email address within 2 minutes. If the email does not arrive, ask the customer to check their spam folder and verify the address is spelled correctly.
3. The reset link is valid for 60 minutes. After 60 minutes the link expires and a new one must be requested - the expired link cannot be extended.
4. After completing the reset, all active sessions on other devices are automatically invalidated. The customer will need to log in again on any devices where they were previously signed in.

**If the customer's email address is unreachable:**
Support can send the reset link to a verified secondary phone number on file via SMS. The customer must contact support to initiate this - it cannot be triggered by the customer directly. SMS reset links are valid for 15 minutes.

**If both email and phone are unavailable:**
The customer must submit an identity verification request. Required documentation: government-issued photo ID and one form of account ownership proof (original signup email address, billing information on file, or a verifiable order from the account history). Identity verification takes 3–5 business days from submission of complete documentation. This process cannot be expedited.

## 2. Two-Factor Authentication Setup

2FA can be enabled from Settings > Security > Two-Factor Authentication.

**TOTP authenticator app (recommended)**
Compatible with Google Authenticator, Authy, Microsoft Authenticator, and any RFC 6238-compliant TOTP application. During setup, a QR code is displayed - scanning it registers the account in the app. After scanning, the customer enters the 6-digit code shown in the app to confirm the connection before 2FA is activated.

**SMS**
A 6-digit verification code is sent to the verified phone number on file at each login. SMS 2FA is available as a secondary option. Customers who ask which method to use should be informed that TOTP apps are more secure than SMS because SMS codes are vulnerable to SIM-swap attacks, where an attacker convinces a carrier to transfer the phone number.

**Backup codes**
During 2FA setup, 8 single-use backup codes are generated and displayed once. These are the only recovery method available if the 2FA device is lost or inaccessible. The customer should store them in a secure, offline location. Backup codes can be regenerated from Settings > Security while the account is accessible, but regenerating a new set immediately invalidates all previous codes.

## 3. Two-Factor Authentication Recovery

**If the customer has a backup code:**
At the 2FA prompt, the customer clicks "Use a backup code" and enters one of their 8 saved codes. Each code is single-use. After entry, the code is invalidated and the remaining codes continue to function.

**If all backup codes are lost and the 2FA device is unavailable:**
The customer must complete the identity verification process described in Section 1. There is no way to bypass 2FA recovery without completing identity verification - this requirement exists to prevent account takeover. The process takes 3–5 business days from submission of complete documentation.

**If the customer's phone number changed and SMS 2FA is active:**
A phone number associated with SMS 2FA can only be updated by a customer who is currently authenticated. If the customer is locked out because the old phone number is no longer accessible, they must go through identity verification. Support cannot update the phone number without verification.

## 4. Account Suspension Reasons

**Payment failure**
The account is suspended automatically when payment has not been received after the full retry cycle completes (Day 8 following the original failed charge - see Billing FAQ, Section 4). The customer receives the following notification:

*"Your account has been suspended due to a payment issue. To restore access, please update your payment method from the login page or contact support. Your data will be retained for 30 days from the date of suspension."*

**Terms of service violation**
Accounts found to be in violation of the terms of service are suspended pending review. Violations include unauthorized automated access (scraping, bots), platform abuse, fraudulent activity, and harassment of support staff or other users. The customer receives the following notification:

*"Your account has been suspended pending review. If you believe this action was taken in error, you may submit an appeal by replying to this email within 14 days."*

**Suspicious activity detection**
Accounts are automatically suspended when the system detects login patterns consistent with unauthorized access: logins from multiple geographic locations within a short window, mass data export events, or credential stuffing indicators. The customer receives the following notification:

*"Your account has been temporarily locked due to unusual activity. This is a precautionary measure to protect your account. Please verify your identity to restore access."*

**Data compliance hold**
In certain jurisdictions or circumstances, accounts may be placed on hold pending a legal or compliance review. This is initiated internally and not triggered by customer action. The customer receives the following notification:

*"Your account access has been temporarily restricted. Please contact support for more information."*

Support agents handling a compliance hold should not provide details about the nature or basis of the hold. Confirm only whether the hold is still active and direct the customer to contact support.

## 5. Reactivation Process

**Payment-related suspension (self-serve):**
1. Go to the login page. A suspended account due to payment failure will display a "Restore Access" link below the login form.
2. Click "Restore Access" and navigate to the payment update screen - this is the only page accessible on a suspended account.
3. Enter a valid payment method and submit. An immediate charge attempt is made.
4. If the charge succeeds, access is restored within 5 minutes and a confirmation email is sent.
5. If 30 days have passed since the suspension date, the self-serve path is no longer available. The customer must contact support.

**Terms of service violation (requires review):**
The customer submits an appeal by replying to the suspension notification email. Appeals must include a statement explaining why the customer believes the suspension was made in error. A review is completed within 24–48 hours. The outcome is one of three: access restored with a written warning, access restored with specific feature restrictions, or account permanently closed. The decision is communicated by email and is final for first-level review.

**Suspicious activity hold (requires identity verification):**
The customer completes identity verification from the locked account screen. The verification prompt is accessible by clicking "Verify Identity" on the account locked page. Upon successful verification, access is restored immediately, all existing sessions are invalidated, and a forced password reset is triggered before re-entry.

**Data compliance hold:**
Reactivation is not available through self-serve or standard support channels while a compliance hold is active. The customer must contact support to confirm current hold status. Support cannot share information about the nature of the hold.

## 6. Data Deletion and Privacy Requests

**Applicable rights**
Customers covered by GDPR (European Economic Area residents) have the right to erasure under Article 17. Customers covered by CCPA (California residents) have the right to deletion under Section 1798.105. Both rights are honored for all customers regardless of location.

**How to submit a request**
- From an active account: Settings > Privacy > Request Data Deletion
- From an inactive, suspended, or inaccessible account: submit a written request to the privacy contact email address listed in the current Privacy Policy

**Fulfillment timeline**
Deletion requests are processed within 30 calendar days of receiving a confirmed request. A confirmation email is sent when the request is logged and a second confirmation is sent when deletion is complete. Requests that require identity verification before processing (for inactive accounts) begin the 30-day clock from the date verification is completed, not the date of the initial request.

**What is deleted**
Account profile and preferences, billing payment method data, usage and activity history, uploaded or stored content, and identifiable metadata attached to account events.

**What is retained**
Transaction records and invoices are retained for 7 years to meet tax and financial compliance requirements. Aggregated, anonymized usage data that cannot be re-linked to the individual is retained for product analytics. Records subject to an active legal hold are retained until the hold is released.

**Holds on deletion**
Data deletion cannot be processed while a financial dispute or chargeback is open, while an account is under a legal or compliance hold, or while a fraud investigation is active. The deletion request is queued and processed automatically once the blocking condition is resolved.

After a deletion request is completed, the account cannot be restored, and all associated content, purchase history, and subscriptions are permanently removed.
