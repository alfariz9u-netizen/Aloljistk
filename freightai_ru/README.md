# FreightAI MVP — Telegram Freight Matching Bot (Russian / Kyrgyzstan build)

**This is the Russian-language, Kyrgyzstan-localized build.** Same
architecture, security model, and matching logic as the original
Saudi/Arabic version — only the language (all bot-facing text is in
Russian) and the city list (`backend/app/services/cities.py`, now
Kyrgyzstan cities: Бишкек, Ош, Джалал-Абад, Каракол, Токмок, and others)
differ. See `backend/app/services/cities.py` to add more cities/regions
as needed.

A minimal, secure, concurrency-safe MVP that connects truck owners
looking for loads with load owners looking for carriers, entirely
through a Telegram bot. The platform admin mediates the final contact —
this is **not** an open marketplace where users message each other
directly.

Built fresh for this scope (not a trimmed copy of any larger reference
project). Explicitly out of scope for this MVP: payments, pricing
engine, invoices, documents, dashboard/frontend, ML, and advanced
marketplace features. The codebase is modular so those can be added
later without rewriting the core.

## Architecture

```
Telegram user
     │
     ▼
  bot (aiogram 3, long polling)
     │  HTTP + X-Bot-Secret header
     ▼
  backend (FastAPI)  ──┬── PostgreSQL (source of truth)
     │                 └── Redis (rate limiting, locks, FSM state)
     ▼
  worker (reminders + proactive/backhaul matching, polls every 60s)
```

- **backend/** — FastAPI app: registration, truck/load creation,
  deterministic matching engine, notifications, admin mediation.
- **bot/** — aiogram 3 Telegram bot: conversational flows, free-text
  intake with AI-assisted extraction + mandatory user confirmation,
  manual fallback, admin panel commands.
- **tests/** — pytest suite covering matching, concurrency, security,
  notifications, and registration.

## Quick start

```bash
cp .env.example .env
# edit .env: set TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_CHAT_ID,
# BOT_SERVICE_SECRET (openssl rand -hex 32), and optionally
# ANTHROPIC_API_KEY for free-text extraction.

docker compose up --build
```

Then in Telegram:
1. Message your bot, `/start`.
2. Choose "🚛 Я перевозчик (есть машина)" or "📦 Я грузовладелец (есть груз)".
3. Give your name and phone (only ever seen by the admin).
4. Describe your truck/load in free text, or let it fall back to a
   step-by-step manual form if extraction can't parse it confidently.
5. Confirm the extracted summary before anything is saved.

As the admin (the account whose Telegram numeric chat ID matches
`TELEGRAM_ADMIN_CHAT_ID` on its *first* `/start`), run `/admin` to see
open loads, available trucks, and pending matches, with buttons to view
contact info, mark a match connected, or reject it.

To promote a different account to admin later (rather than relying on
the env-var bootstrap), use the operator script — this is the *only*
other way admin is ever granted, there is no self-promotion path via
the bot or API:

```bash
docker compose exec backend python -m scripts.promote_admin <telegram_id>
```

## Core workflow (scenario 1 — carrier registers, load already waiting)

```
"Моя машина в Бишкеке, ищу груз до Оша"
  → extraction → confirmation → saved
  → matching engine checks WAITING_FOR_MATCH loads
  → match found → notify carrier, shipper, admin (no phone numbers exchanged)
  → admin runs /admin → 📞 Связаться → connects both parties
```

## Core workflow (scenario 2 — load registers, no carrier yet)

```
"Есть груз, 3 машины, из Бишкека в Ош"
  → no matching truck right now
  → load = WAITING_FOR_MATCH, shipper told "still searching"
  → filtered broadcast to eligible carriers with a "🚛 Меня интересует" button
  → 10 minutes later: one reminder to carriers who haven't responded
  → carrier presses "interested" → admin + shipper notified → admin connects
```

## Core workflow (scenario 3 — proactive/backhaul)

```
Carrier reports an active trip: Bishkek → Osh, ETA in 8h
  → truck.status = ON_TRIP, trip_destination = Ош, trip_eta = now+8h
  → background worker scans ON_TRIP trucks within ±60 min of ETA
  → finds a WAITING_FOR_MATCH load originating from Ош
  → proactive match created, shipper + carrier + admin notified
```

## Carrier growth system (daily quota + referrals + priority tiers)

Every carrier gets a **daily allowance of Broadcast notifications**
("here's a new load looking for any carrier") — scoped deliberately to
Broadcast only, never to direct/proactive matches, so quota never costs
a shipper their best real match (see `services/quotas.py`):

- **Base tier**: 1 free Broadcast notification per day.
- **Referral tier**: +5 to the daily quota per successful referral
  (someone who registered via your link — see `/invite` below — AND
  completed real registration, i.e. submitted a phone number, not just
  pressed `/start`). Referral count also breaks ties for priority order
  among non-subscribers.
- **Subscriber tier**: unlimited quota, always notified first about any
  new load (see `carrier_priority_sort_key`). Phase 1 activation is
  manual — the admin runs `/subscribe <telegram_id> [days]` in Telegram
  after collecting payment outside the bot (see the Payments section
  above for why cross-border automated collection needed to wait).

Carriers see their own standing (referral link, referral count, daily
quota usage) via the `/invite` command.

Priority tiers only affect **order** (who's notified first, and who's
skipped once quota is scarce) — every eligible carrier still gets a
fair shot at every load their tier's quota allows; nothing is ever
hidden from a shipper's load to protect a paying carrier's exposure.

See `tests/test_quotas_referrals.py` for the quota/priority/referral
behavior under test.

## Premium auto-connect (dormant by default — reduces admin protection, read this first)

Ships **off**: `AUTO_CONNECT_PREMIUM_ENABLED=false` in `.env.example`
means every fresh match still goes through normal admin-mediated
review, exactly like the base project — the only existing bypass is an
established `TrustedPair` (a relationship the admin already vetted
once).

When turned on, a match where the **carrier** is a paid subscriber or
has ≥1 successful referral (see the Carrier growth system section
above) skips admin mediation entirely and auto-connects immediately —
contact info revealed to both sides right away, no admin tap required.
This applies to direct matches and interest-registered matches
("🚛 Меня интересует" on a Broadcast) — **never** to proactive/backhaul
matches, which stay admin-mediated regardless of this setting or the
carrier's tier, since they already carry more inherent uncertainty
(approximate ETA windows).

**Read this trade-off before enabling it**: admin mediation isn't just
a bureaucratic step — it's the platform's only fraud/quality screening.
Turning this on means a **shipper** who has never dealt with a given
premium carrier before, and who never chose to skip that screening
themselves, has it skipped on their behalf the moment that carrier's
tier qualifies. Unlike `TrustedPair` (earned through a specific,
already-vetted relationship), this is a blanket exemption tied to the
carrier's account status alone. Only enable this once you're
comfortable with that exchange — speed and a premium perk, for reduced
screening on that carrier's matches.

See `tests/test_premium_auto_connect.py` for the exact gating behavior.

## Payments (Phase 2 monetization — dormant by default)

Ships **off**: `PAYMENTS_ENABLED=false` in `.env.example` means contact
reveal stays exactly as free as the base project, forever, until you
turn it on.

When flipped on (`PAYMENTS_ENABLED=true`), the admin's `📞 Связаться`
(contact) action gates on payment: instead of revealing phone numbers,
the admin sees the fee amount and who owes it (`PAYMENT_CHARGE_PARTY` —
`carrier` or `shipper`), relays payment instructions to that person
outside the bot (bank transfer, Elcart card-to-card, cash), and taps
`✅ Оплата подтверждена` once the money has actually arrived — only then
does contact info unlock. **Trusted pairs are always exempt**, even with
payments on — they already paid once under admin supervision, so
re-charging a known, vetted relationship on every repeat match would be
exactly the friction `TrustedPair` exists to remove (see the Trusted
pairs section above).

This is Phase 1 (manual, admin-confirmed) by design — no payment
gateway integration required to start charging. See
`backend/app/services/payments.py` for the gating logic and
`backend/app/services/payment_providers.py` for the documented
extension point for an automated gateway later (Freedom Pay Kyrgyzstan
is the leading candidate — it already supports in-Telegram card
payments): implementing `PaymentProvider` there and wiring a webhook is
the only change needed: the `Payment` model, the unlock condition, and
the admin UI don't change between phases, since gateway automation just
replaces *how* a `Payment` row goes from `PENDING` to `CONFIRMED` (a
verified webhook instead of an admin tap).

Money is always stored as an integer in the currency's smallest unit
(tyiyn for KGS — `PAYMENT_CONTACT_FEE_AMOUNT=5000` means 50.00 KGS),
never a float, to avoid rounding-error bugs. See
`tests/test_payments.py` for the gating behavior under test.

## Trusted pairs (repeat partners skip the manual admin step)

Requiring the admin to manually approve every single match — even
between two people who've already worked together before and already
have each other's phone number — is pure friction with no privacy
benefit. So:

- The **first** time a shipper and a carrier are matched, it always
  goes through the normal flow: `PENDING` match, no contact info
  shared, admin reviews via `/admin` and presses `✅ Связано`.
- That action records a `TrustedPair` for that specific shipper +
  carrier combination (`backend/app/services/trusted_pairs.py`).
- Any **future** match between that exact same pair is auto-connected
  immediately — `CONNECTED` status, both parties notified directly with
  each other's name and phone. This is the one deliberate exception to
  "never reveal contact info automatically" — and it's safe precisely
  *because* it isn't new exposure: they already exchanged that info
  once, under admin supervision.
- Trust is scoped to the pair, never generalized — a carrier trusted
  with one shipper is not auto-connected with a different shipper.
- Proactive/backhaul matches and interest-based matches (broadcast →
  "🚛 Меня интересует") intentionally still go through admin review even for
  trusted pairs, since those involve more uncertainty (approximate ETA
  windows, unsolicited interest) than a direct route match — this can
  be extended later if desired.

See `tests/test_trusted_pairs.py`.

## Free single-service hosting (no VPS, no credit card charges)

The default `docker compose up --build` setup above needs an always-on
host (a VPS, Oracle Cloud Always Free, etc.) because it runs 3 long-lived
processes: `backend`, `bot` (long-polling), and `worker` (a loop that
never exits). Most genuinely free hosts (Render's free tier, etc.) only
offer a **request-driven web service** that sleeps between requests and
give **no free background-worker tier** — a separate always-running
`bot`/`worker` simply isn't possible there.

`Dockerfile.webapp` collapses everything into **one** request-driven web
service instead, so it fits that model:

| Piece | Normal (docker-compose) | Free single-service mode |
|---|---|---|
| Bot transport | long polling (own process) | Telegram **webhook** → `POST /telegram/webhook`, handled by the same FastAPI app |
| Reminders / proactive matching | standalone `worker` loop | external free pinger calls `POST /internal/cron/tick` every few minutes |
| What keeps it "alive" | nothing needed, it's a VM | the same ping that runs the cron tick also keeps the service warm |

Nothing in the matching/notification/security logic changes — same
code, same database, same tests. Only the transport for the bot and the
trigger for the periodic jobs differ, both controlled by `BOT_MODE` (see
`.env.example`).

### 1. Get free managed Postgres + Redis

Render/Fly's free web-service tier doesn't include a persistent database
either, so use separate free managed services instead:
- **Postgres**: [Supabase](https://supabase.com) or [Neon](https://neon.tech) free tier — copy the `postgresql://` connection string, and change its scheme to `postgresql+asyncpg://` for `DATABASE_URL`.
- **Redis**: [Upstash](https://upstash.com) free tier (serverless Redis, works over TLS) — copy the `rediss://` connection string for `REDIS_URL`.

### 2. Deploy `Dockerfile.webapp`

On your host of choice (Render, Fly.io, etc.), create a new web service
pointing at this repo, telling it to build `Dockerfile.webapp` at the
repo root (not `backend/Dockerfile`). Set these environment variables
(see `.env.example` for the full annotated list):

```
ENVIRONMENT=production
DATABASE_URL=postgresql+asyncpg://...        # from Supabase/Neon
REDIS_URL=rediss://...                        # from Upstash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_CHAT_ID=...
BOT_SERVICE_SECRET=<openssl rand -hex 32>
BACKEND_BASE_URL=http://127.0.0.1:8000        # bot talks to itself, same process
BOT_MODE=webhook
PUBLIC_BASE_URL=https://<your-service>.onrender.com   # your host assigns this
TELEGRAM_WEBHOOK_SECRET=<openssl rand -hex 32>
CRON_SECRET=<openssl rand -hex 32>
```

On startup, the app registers itself with Telegram as the webhook target
automatically (see `app/main.py`) — no manual `setWebhook` call needed.

### 3. Point a free external pinger at `/internal/cron/tick`

Use a free scheduled-HTTP service such as [cron-job.org](https://cron-job.org)
to send this every 5 minutes:

```
POST https://<your-service>.onrender.com/internal/cron/tick
Header: X-Cron-Secret: <the CRON_SECRET you set above>
```

This single ping does double duty: it drives the reminder + proactive-
matching pass (see `app/api/cron.py`), **and** its own HTTP request is
what keeps a sleep-after-idle host warm — no separate "keep-alive" ping
needed on top of it.

### Trade-offs of this mode, honestly

- A **cold start** after idle time can delay the very first reply by a
  few seconds (the underlying limitation of a free tier that sleeps —
  no configuration fully eliminates this, only masks it).
- Free managed Postgres/Redis tiers usually have connection/storage caps
  fine for an MVP's traffic, but check current limits on Supabase/Neon/
  Upstash before relying on this for anything beyond testing.
- This is a genuinely good fit for demoing, testing, or low-volume real
  use. For production traffic, prefer the docker-compose + VPS path.

## Matching rules

Origin and destination match (after city-spelling normalization) is
**mandatory** — a load Bishkek→Osh never matches a truck
Karakol→Osh, regardless of score. Optional signals (truck type,
count, availability) only adjust a 0–100 score on top of that. Matching
is 100% rule-based in the backend; AI is never involved in the matching
or authorization decision (see `backend/app/services/matching.py`).

## Security highlights

See `SECURITY.md` for the full list. Summary:
- Every backend `/internal/*` route requires a shared `X-Bot-Secret`
  header known only to the bot process — the API is not exposed to
  arbitrary clients.
- Admin status lives in `users.role` in the database, never in a
  Telegram username, and is only ever set by the one-time bootstrap
  (matching `TELEGRAM_ADMIN_CHAT_ID` on first `/start`) or the
  `scripts/promote_admin.py` operator script — no API path lets a user
  self-promote.
- Ownership is checked server-side on every mutation (e.g. registering
  interest requires the caller's own truck).
- Phone numbers are never sent to either party automatically — only the
  admin ever sees them, through an audited `contact` action.
- AI is used strictly for text → structured-JSON extraction, output is
  re-validated against a strict Pydantic schema, and it never touches
  the database, sends messages, or makes an authorization/matching
  decision.
- Concurrent match/notification creation is race-safe via database
  unique constraints, not in-memory checks (see `tests/test_concurrency.py`).
- Rate limits (messages/min, loads/hour, trucks/hour, AI calls/hour)
  are enforced server-side, keyed by `telegram_id`.

## Running tests

```bash
cd backend && pip install -r requirements-dev.txt
cd .. && pytest tests -v
```

Tests use an in-memory SQLite database (same SQLAlchemy models,
same unique constraints) so they run without needing Postgres/Redis —
see `tests/conftest.py`.

## Environment variables

See `.env.example` for the full list with comments. Never commit `.env`.

## Project layout

```
freightai/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/           config, database, redis, logging, auth
│   │   ├── models/         SQLAlchemy models
│   │   ├── schemas/        Pydantic request/response + AI-output schema
│   │   ├── services/       matching, notifications, extraction,
│   │   │                   proactive matching, rate limiting, audit,
│   │   │                   telegram client, city normalization
│   │   ├── api/            users, trucks, loads, interests, admin,
│   │   │                   extract, health
│   │   └── workers/        reminder + proactive-matching scheduler
│   ├── alembic/            migration scaffolding (MVP auto-creates
│   │                       tables on startup; switch to alembic for prod)
│   ├── scripts/            promote_admin.py operator script
│   └── requirements*.txt, Dockerfile
├── bot/
│   ├── bot/
│   │   ├── main.py
│   │   ├── handlers/       start, intake (free-text + manual fallback),
│   │   │                   admin
│   │   ├── api_client.py, keyboards.py, states.py, middlewares.py
│   └── requirements.txt, Dockerfile
├── tests/
├── docker-compose.yml
├── .env.example
├── README.md
└── SECURITY.md
```
