# Security Notes

This document treats the system as if it will be exposed to the
internet — because it will be. Assume users will send malformed
payloads, forge callback data, try to reach IDs that aren't theirs,
spam, attempt prompt injection, and try to create duplicate records.

## 1. Trust boundaries

```
Telegram user  ──(untrusted)──▶  bot process (polling) OR Telegram's servers (webhook)
bot process    ──(X-Bot-Secret)──▶  backend API
Telegram servers ──(X-Telegram-Bot-Api-Secret-Token)──▶  POST /telegram/webhook   [BOT_MODE=webhook only]
external pinger  ──(X-Cron-Secret)──▶  POST /internal/cron/tick                    [BOT_MODE=webhook only]
backend API    ──(SQL, parameterized via SQLAlchemy)──▶  PostgreSQL
backend API    ──(prompt, length-capped, no secrets)──▶  Anthropic API
```

- **Telegram → bot**: every text message is untrusted input. Length is
  capped (`MAX_FREE_TEXT_LENGTH`), and a Redis-backed per-user
  messages/minute limit is enforced in `bot/middlewares.py` before a
  handler even runs.
- **bot → backend**: every `/internal/*` endpoint requires the
  `X-Bot-Secret` header (`backend/app/core/security.verify_bot_secret`).
  This proves the caller is our bot process, not an arbitrary HTTP
  client. It is a shared secret, generated with `openssl rand -hex 32`,
  stored only in `.env`, never committed, never sent to Telegram or the
  AI.
- **backend → Postgres**: all queries go through SQLAlchemy's
  parameterized query builder — no raw string interpolation into SQL
  anywhere in the codebase.
- **backend → Anthropic**: only for `/internal/extract`. The AI
  receives the raw user text and nothing else — no secrets, no other
  users' data, no database access, no tool use. Its JSON output is
  parsed and then **re-validated** against `ExtractedLoad` (a strict
  Pydantic schema with type/range constraints) before anything
  downstream touches it. See "Prompt injection" below.
- **Telegram → webhook** (only relevant when `BOT_MODE=webhook`, see
  README.md's free-hosting section): Telegram calls
  `POST /telegram/webhook` directly instead of the bot long-polling for
  updates. The URL is guessable by anyone; what proves a request
  genuinely came from Telegram is the `X-Telegram-Bot-Api-Secret-Token`
  header, which we register as `secret_token` in `setWebhook` on
  startup and re-check on every incoming call
  (`app/api/telegram_webhook.py`). A request with a missing/wrong token
  is rejected with 401 before the update ever reaches the dispatcher.
- **External pinger → cron** (only relevant when `BOT_MODE=webhook`):
  a free scheduled-HTTP service calls `POST /internal/cron/tick` every
  few minutes to run the reminder + proactive-matching pass in place of
  a standalone worker process. Guarded by its own `CRON_SECRET` —
  deliberately a *different* secret than `BOT_SERVICE_SECRET`, so this
  less-trusted, lower-value caller can only ever trigger that one
  narrow, idempotent action, never anything the bot secret can do
  (least privilege). See `app/api/cron.py`.

## 2. Authentication & authorization

- Users are identified by Telegram numeric `telegram_id`, stored as the
  unique key on `users`. There is no username-based or client-supplied
  identity — the bot always uses the `from_user.id` Telegram itself
  supplies in the update, which can't be spoofed by the message text.
- **Admin is never derived from a Telegram username.** It is either:
  1. Set once, automatically, the first time a user whose
     `telegram_id` equals `TELEGRAM_ADMIN_CHAT_ID` calls the upsert
     endpoint (`is_bootstrap_admin_telegram_id` in `core/security.py`),
     or
  2. Set by an operator running `python -m scripts.promote_admin
     <telegram_id>` directly against the database, inside the backend
     container.
  There is **no API path** — not `/internal/users/role`, not any admin
  endpoint — that lets a user grant themselves or anyone else the
  ADMIN role. `SetRoleIn`'s `role` field only accepts the literals
  `"CARRIER"` / `"SHIPPER"` at the schema level, so `"ADMIN"` is
  rejected before it can reach the database.
- Every admin endpoint (`backend/app/api/admin.py`) re-resolves
  `telegram_id → User.role` from the database on every call via
  `_require_admin()`. It does not trust a client-supplied role claim.
- **Ownership checks** are enforced server-side on every mutation, not
  assumed from the bot's UI flow:
  - `POST /internal/interests` verifies the referenced `truck_id`
    actually belongs to the `User` resolved from `telegram_id` before
    registering interest — a forged `truck_id` belonging to someone
    else is rejected with 403.
  - Load/truck listing endpoints (`GET /internal/loads/{telegram_id}`,
    `GET /internal/trucks/{telegram_id}`) only ever return rows scoped
    to that resolved user.
  - Telegram inline-button callback data (e.g. `interest:<load_id>`,
    `admin:contact:<match_id>`) only ever carries an entity ID, never a
    role or permission claim — the actual authorization decision is
    always re-derived server-side from the pressing user's
    `telegram_id`, so a forged/replayed callback can at most reference
    a valid-looking ID, never grant elevated access.

## 3. Privacy — no phone numbers leak between parties

- Match/broadcast/reminder/proactive notification text (see
  `backend/app/services/notifications.py`) never includes a phone
  number, Telegram handle, or other direct-contact detail for the
  other party.
- The only way a phone number is ever revealed is the admin's `📞
  Связаться` (`contact`) action in `/admin`, which is (a) gated by
  `_require_admin`, and (b) written to `audit_logs` via
  `admin_viewed_contact` every time it's used.
- **One deliberate, narrow exception**: once the admin has connected a
  specific shipper + carrier pair once (`✅ Связано`), that pair is
  recorded as a `TrustedPair` (`backend/app/services/trusted_pairs.py`).
  Any future match between that *exact same pair* is auto-connected and
  DOES include contact info in the notification to both parties — see
  `notify_auto_connected()` in `services/notifications.py`. This is
  intentional and safe: they already have each other's number from the
  first, admin-supervised connection, so this reveals nothing new — it
  only removes the friction of re-approving a relationship the admin
  already vetted. Trust is strictly scoped to the (shipper_id,
  carrier_id) pair (`UNIQUE(shipper_user_id, carrier_user_id)`), never
  inferred automatically, and only ever created from the admin's
  `connect` action — no API path lets either party mark themselves as
  trusted with someone else. Every auto-connect is still audit-logged
  (`auto_connected_trusted_pair`). See `tests/test_trusted_pairs.py`.

## 4. Prompt injection

The extraction system prompt (`backend/app/services/extraction.py`)
explicitly instructs the model to treat the entire user message as
inert data, never as instructions, and to ignore any embedded
instruction to break out of the JSON schema. But the actual security
guarantee does **not** rely on the model obeying that instruction:

- The AI has no tools, no database access, and cannot send Telegram
  messages. It can only return text, which is parsed as JSON and then
  validated against `ExtractedLoad`.
- `ExtractedLoad` uses `model_validate` under `extra="ignore"` semantics
  (Pydantic default) — any field the model invents outside the declared
  schema (e.g. an attempted data-exfiltration field) is silently
  dropped, never propagated.
- Every field has an explicit type and, where relevant, a bounded range
  (`truck_count` 1–50, `confidence` 0.0–1.0) — out-of-range or
  wrong-typed values raise a validation error and the caller falls back
  to manual entry rather than accepting garbage.
- If extraction fails for any reason — bad JSON, API error, timeout,
  schema violation — `extract_from_text()` catches the exception,
  logs it, and returns a neutral `type="unknown", confidence=0.0`
  result. The bot then falls back to the manual step-by-step flow. The
  system never crashes or blocks on an AI failure.
- See `tests/test_security.py::test_extraction_output_always_validated_against_strict_schema`
  for a concrete adversarial-input test.

## 5. Rate limiting

Enforced server-side, keyed by `telegram_id` (not a client header, so
it can't be bypassed), via Redis (`backend/app/services/rate_limit.py`):

| Scope | Default limit |
|---|---|
| messages | 30 / minute |
| load creation | 10 / hour |
| truck creation | 10 / hour |
| AI extraction | 15 / hour |

All tunable via environment variables (see `.env.example`). The bot
additionally applies its own lightweight messages/minute throttle
(`bot/middlewares.py`) purely to keep the bot responsive under spam —
this is UX protection, not the security boundary (the backend limits
are, since they can't be bypassed by talking to the API directly
without the shared secret).

## 6. Concurrency / duplicate prevention

Every place where a duplicate row would be a real problem is protected
by a **database unique constraint**, not an in-memory or
"check-then-insert" pattern:

- `matches`: `UNIQUE(load_id, truck_id)` — two simultaneous requests
  trying to create the same match (e.g. a new-truck scan and the
  regular load-matching flow racing) result in exactly one row; the
  loser catches `IntegrityError`, rolls back its `SAVEPOINT`, and
  fetches the row the winner created instead of erroring.
- `notifications`: `UNIQUE(user_id, load_id, notification_type)` — the
  same broadcast/reminder/match notice can never be double-queued for
  the same user about the same load, even across process restarts or
  retries.
- `interests`: `UNIQUE(load_id, truck_id)`.
- `users`: `UNIQUE(telegram_id)`.

See `tests/test_concurrency.py` for tests that fire several concurrent
match-creation attempts and assert exactly one row survives, and
`tests/test_registration.py` for the same guarantee on user
registration.

The background worker (`backend/app/workers/scheduler.py`) uses a
Postgres advisory lock (`pg_try_advisory_lock`) per job, so running
multiple worker replicas never causes the same reminder/proactive-match
pass to execute twice concurrently — replicas that lose the lock simply
skip that tick.

## 7. Reliability

- Telegram sends retry with exponential backoff (1s, 2s, 4s, 8s) on
  transient/5xx failures, then give up and mark the notification
  `FAILED` rather than blocking the caller
  (`backend/app/services/telegram_client.py`). 4xx failures (e.g. bot
  blocked by user) are not retried.
- No `except Exception: pass` anywhere — failures are always logged
  via the structured logger (`backend/app/core/logging_config.py`),
  including audit-log write failures, which never block the action
  they describe but are logged loudly.
- Global unhandled-exception handler in `main.py` returns a generic
  Arabic error message to the client in production while logging the
  real exception server-side — no stack traces are ever exposed to
  users.
- `GET /health`, `/health/db`, `/health/redis` for basic liveness
  checks.

## 8. Secrets

- No token, password, or API key is hardcoded anywhere in the
  codebase. All come from environment variables (`.env`, never
  committed — see `.dockerignore` / `.gitignore` conventions).
  `backend/app/core/config.py` refuses to start in
  `ENVIRONMENT=production` if `BOT_SERVICE_SECRET` is still the
  insecure default, or if `TELEGRAM_ADMIN_CHAT_ID` is unset.
- Logs never include tokens, phone numbers, or full user payloads —
  only IDs and action names.

## 9. Carrier growth system (daily quota, referrals, priority tiers)

- Referral rewards only fire on a real completed registration (first
  phone number submission), never on a bare `/start` — see
  `services/referrals.py`'s anti-abuse rationale. A user can never
  self-refer (rejected both at parse time in the bot and again
  server-side in `set_referred_by`).
- `referred_by_user_id` is set exactly once, at first row creation, and
  is never editable afterwards through any endpoint.
- `reward_referral_if_needed` is idempotent by design
  (`referral_rewarded` flag) — it cannot be triggered twice for the
  same referred user, so there's no way to farm points by re-submitting
  a phone number.
- Subscription activation (`grant_subscription`) is an ADMIN-only action
  (re-verified server-side like every other admin action, see section
  1) — no endpoint lets a user activate their own subscription.
- Priority tiers affect notification **order** and which carriers get
  skipped once daily quota is scarce — they never limit a *shipper's*
  chance of reaching their best-matching carrier: quota/priority is
  scoped to Broadcast notifications only, never to direct or proactive
  matches (see `services/quotas.py`'s module docstring).

## 10. Premium auto-connect (dormant, deliberately reduces screening when on)

`AUTO_CONNECT_PREMIUM_ENABLED=false` by default. This is the one
feature in this codebase that, when turned on, INTENTIONALLY reduces
the platform's fraud/quality screening for a subset of matches — worth
calling out explicitly here rather than only in README.md, since every
other section of this document is about *maintaining* security
guarantees, and this one is a conscious, opt-in trade against one.

- Scope is as narrow as the feature allows: only the CARRIER side's
  tier is checked (subscription or ≥1 referral), and only for direct
  and interest-registered matches — proactive/backhaul matches are
  hard-excluded in code (`notify_proactive_match` never calls
  `should_skip_admin_mediation`), not just by convention.
- The exemption is tied to the carrier's *account status*, not to a
  specific vetted relationship — unlike `TrustedPair`, which only
  exempts a match between two *specific* people the admin already
  connected once. A premium carrier's very first match with a brand
  new shipper can skip admin review under this setting; `TrustedPair`
  never permits that.
- No user can grant themselves this exemption: subscription is
  admin-only (`grant_subscription`, itself server-side admin-verified),
  and referral_points only increase via the anti-abuse-gated reward
  path in `services/referrals.py`.
- If you enable this, understand you are trading the admin's screening
  for speed on that carrier's matches — see README.md's "Premium
  auto-connect" section for the full explanation before turning it on.

## 11. Payments (Phase 2, dormant by default)

`PAYMENTS_ENABLED=false` by default — nothing in this section applies
until it's explicitly turned on.

- No payment decision is ever automated in Phase 1: a `Payment` only
  becomes `CONFIRMED` via an explicit admin tap
  (`confirm_payment_manual` in `services/payments.py`), after the admin
  has personally verified, outside the bot, that money actually
  arrived. There is no self-service "I paid" button that unlocks
  anything by itself — a payer claiming payment is never sufficient on
  its own.
- The contact-reveal gate (`is_contact_unlocked`) is the single choke
  point every path (direct match, interest-based match, proactive
  match) goes through — so there's no route that bypasses payment once
  enabled, other than the deliberate `TrustedPair` exemption (itself
  only earned via a prior admin-confirmed connection, never
  self-granted — see the Trusted pairs section above).
- Money amounts are always integers in the currency's smallest unit,
  never floats — eliminates an entire class of rounding-error bugs
  common in payment code.
- The Phase 2 gateway extension point (`payment_providers.py`)
  explicitly documents that any future webhook endpoint must verify
  authenticity (signature/secret) before trusting the payload, mirroring
  `api/telegram_webhook.py`'s pattern — this is a requirement on the
  future implementation, not yet exercised code, since no gateway is
  wired up yet.

## 12. What this MVP deliberately does NOT include

Per project scope: no payments, no pricing/financial engine, no
frontend/dashboard, no Google Maps integration, no ML-based matching,
no invoices/documents, no advanced marketplace features. Matching is
100% deterministic, rule-based backend logic — AI is never involved in
authorization, authentication, admin detection, ownership, permissions,
financial decisions, match confirmation, or privacy decisions (only in
free-text → structured-data extraction, always followed by explicit
user confirmation before anything is persisted).
