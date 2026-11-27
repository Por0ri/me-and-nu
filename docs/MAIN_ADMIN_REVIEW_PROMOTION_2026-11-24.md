# Main admin review promotion

On 2026-11-24 the service owner authorized promoting Checking functionality to Main and completing the work before Checking branch cleanup. The candidate adds an explicit approval record for `https://api.me-nu.kr` to the admin review UI gate while retaining the existing Checking approval. It does not grant any account an admin role.

The approved HTTPS origins are `https://api.me-nu.kr` and `https://api.checking.me-nu.kr`. Other hosts, protocols and non-default ports remain blocked. Credential-bearing URLs are rejected. Environment variables, browser storage and query parameters cannot add an approved origin. The existing explicit fixture override remains scoped to one origin; `null` disables it.

The review client, backend grant and actor checks, CSRF, version/state checks, idempotency and audit behavior are unchanged. Main's administrator withdrawal protection must be retained in the merged backend. Checking resources and its gate remain available until operational cutover verification completes.

## Deployment and verification

Main frontend must be built with `NEXT_PUBLIC_API_BASE_URL=https://api.me-nu.kr`. Next.js embeds public values at build time, so reusing a frontend artifact built for Checking does not switch it to Main. Keep Main's production frontend origin, OAuth callbacks and session-cookie configuration.

Before retiring the Checking API, verify Main's deployed backend/schema and the real OAuth/admin grant contract. Verify approval and rejection using designated synthetic content, preserving audit records and avoiding existing service content. Confirm viewer restrictions, actor/grant scope and a single audit row for a repeated identical request. Local HTTP fixtures are rehearsal evidence, not proof of successful production deployment.

This note records code authorization and the candidate's scope. It does not claim Main deployment, production writes, live OAuth verification, or Checking cleanup has completed. Validation results and any remaining blockers belong in the main promotion report.

## Local validation

On 2026-11-24, the focused Node suite passed 60 tests with no failures or skips:

```text
node --test tests/admin-review-e2e.test.cjs tests/admin-content.test.cjs tests/main-domain-config.test.cjs
```

The suite exercises the real review client against loopback HTTP fixtures. It verifies an audited actor/grant-scoped POST for each approved origin, viewer no-write behavior, unknown origin blocking, credential-bearing URL rejection, and existing CSRF/idempotency/error handling. The browser rehearsal script passes `node --check`; the real browser rehearsal and production verification have not been run for this candidate.
