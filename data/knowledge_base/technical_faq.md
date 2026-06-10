# Technical FAQ

## Common Error Codes

### ERR-401: Authentication Failure

The session token is invalid or expired. This occurs after 24 hours of inactivity, immediately following a password change, or after a forced logout triggered by a security event on the account.

**Resolution steps:** Ask the customer to log out completely (not just close the tab), clear browser cookies and cached data for the domain, and log in again. If the error appears immediately after a fresh login, the account password should be reset - a password reset invalidates all existing session tokens and forces a clean session.

---

### ERR-403: Permission Denied

The account does not have access to the requested resource or feature. Common causes: the customer is attempting to access a feature not included in their subscription tier, or the account has an active restriction applied by an admin.

**Resolution steps:** Confirm the customer's current plan and check whether the requested feature is listed as included. If the plan is correct, check the account for any active restrictions in the admin panel. If neither applies and the feature should be accessible, escalate to Tier 2 with the account ID and the specific resource URL or feature name.

---

### ERR-404: Resource Not Found

The requested item, page, or data record does not exist at the requested location. This can occur when a URL is entered incorrectly, when a shared link points to a deleted item, or when a resource was moved.

**Resolution steps:** Confirm the URL or item ID is correct. If the customer believes the resource should exist, check the account activity log for a deletion event. Deleted resources cannot be restored from the customer-facing interface. If the item was deleted within the last 7 days, escalate to engineering - a database-level restore may be possible within that window.

---

### ERR-429: Rate Limited

The account has exceeded the maximum number of allowed API or application requests within a rolling time window. Rate limits are 100 requests per minute for Standard accounts and 500 requests per minute for Enterprise accounts.

**Resolution steps:** The rate limit resets automatically after 60 seconds from the first request that triggered the limit. No manual action is required on the account. If the customer is hitting this limit consistently, ask them to review their usage patterns. If they are on a Standard plan and their use case legitimately requires higher throughput, route them to the sales team for Enterprise plan evaluation.

---

### ERR-503: Service Unavailable

The service is temporarily unavailable. This can occur during a planned maintenance window or an active unplanned incident. This error is not account-specific - it affects all users.

**Resolution steps:** Check the status page at status.[domain].com before responding. If an active incident is posted, read the incident title and current status update to the customer verbatim. Do not speculate on the root cause, internal details, or estimated resolution time beyond what is stated on the status page. If no incident is posted, collect the customer's timestamp, affected feature, and geographic location and report it through the internal outage reporting channel. Log the interaction to the active incident ticket if one exists.

---

## Connectivity Troubleshooting

Use the following steps in order before escalating a connectivity issue. Ask the customer to confirm each step before moving to the next.

1. **Clear browser cache and cookies.** Stale cached files and expired session cookies are the most common cause of intermittent failures. Use Ctrl+Shift+Delete on Windows or Cmd+Shift+Delete on Mac in most browsers to open the clear browsing data dialog.

2. **Disable browser extensions and VPN.** Ad blockers, security extensions, and VPN clients can interfere with authentication tokens and API requests. Ask the customer to test in incognito or private browsing mode, which disables most third-party extensions by default. If the issue resolves in incognito, a browser extension is the likely cause.

3. **Check for firewall or network restrictions.** Corporate networks and managed devices sometimes block the domains required by the application. Ask the customer if they are on a corporate or school network. If yes, ask them to test on a personal device or mobile data. A list of required domains for allowlist configuration is available in the internal technical reference.

4. **Test on a different network.** If the issue occurs on one network but not another (e.g., fails on corporate WiFi, works on mobile hotspot), the issue is network-level and outside the application's control. Advise the customer to contact their IT department.

5. **Test on a different browser or device.** If the issue is browser-specific, it is likely a compatibility conflict or an extension interaction. See the Device Compatibility section for supported browsers. If no supported browser resolves it, escalate with browser version, OS version, and a screenshot of the error.

If all five steps fail, collect the following before escalating: browser name and version, operating system and version, screenshot or copy of the error message, and the approximate time the issue started.

## Account Access Issues

**Account lockout after failed login attempts**
Accounts are automatically locked after 5 consecutive failed login attempts within a 10-minute window. The lockout lasts 15 minutes and lifts automatically - no action is required by the customer or support. After 15 minutes, the customer can attempt login again.

If the customer does not want to wait, or has forgotten their password, direct them to the password reset flow. Completing a password reset unlocks the account immediately.

**Security-triggered account lock**
If an account is locked due to a security flag rather than failed attempts, it displays a distinct message: *"Your account has been temporarily locked due to unusual activity. This is a precautionary measure. Please verify your identity to restore access."* This type of lock cannot be resolved by waiting - it requires identity verification. See the Account FAQ for the verification process. Do not attempt to unlock a security-flagged account through the standard failed-attempt unlock process.

## Device Compatibility

**Supported desktop operating systems:** Windows 10 or later; macOS 12 (Monterey) or later.

**Supported mobile operating systems:** iOS 15 or later; Android 11 or later.

**Supported browsers:** Chrome 110 or later, Firefox 110 or later, Safari 16 or later, Edge 110 or later.

**Not supported:** Internet Explorer (all versions), Windows 7, Windows 8, macOS 11 (Big Sur) and earlier, iOS 14 and earlier. Issues reported from unsupported configurations are not guaranteed to be reproducible or resolvable by support.

Opera and other Chromium-based browsers generally function but are not officially tested. If a customer on an unsupported browser reports an issue, ask them to reproduce it on a supported browser before investigating further.

## Known Outage Acknowledgment Procedure

When a customer contacts support during an active incident:

1. Verify the incident is listed on the status page before referencing it. Do not tell a customer there is an outage if nothing is posted.
2. Acknowledge the impact using factual language: *"We're aware of an issue affecting [feature]. Our engineering team is currently investigating."*
3. Do not speculate on cause, timeline, or resolution steps beyond what is posted on the status page. Do not say "it should be fixed soon" or give an ETA that is not officially published.
4. Do not offer proactive callbacks or follow-up emails unless the customer requests it and the support tooling supports scheduled follow-ups.
5. Log the customer report against the active incident ticket using the internal incident tracking number. Customer impact reports are used to prioritize engineering response.

If a customer requests compensation or a credit due to outage-related impact, do not commit to anything during the conversation. Inform them that compensation requests are reviewed after an incident is resolved and direct them to submit a request through the account portal once the incident is closed.
