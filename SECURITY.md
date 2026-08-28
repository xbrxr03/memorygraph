# Security Policy

MemoryGraph persists content that may influence future agent behavior. Treat memory poisoning,
prompt injection, source spoofing, cross-bank access, and secret capture as security issues.

The `0.1.0b1` line is a public Beta, not a production-stability release. Please do not publish a
suspected vulnerability before maintainers have had a reasonable opportunity to investigate.

Reports should include:

- Affected commit/version.
- Minimal reproduction.
- Expected and observed behavior.
- Whether untrusted content reached a directive, another bank, or an agent action.
- Suggested mitigation, if known.

Never include real credentials or private user memory in a report. Submit suspected
vulnerabilities through [GitHub private vulnerability reporting](https://github.com/xbrxr03/memorygraph/security/advisories/new)
so the report and follow-up remain private until coordinated disclosure is appropriate.
