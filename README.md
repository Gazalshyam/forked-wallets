# Forked Wallets — AI-Powered Shared Pot

> **PayPal AI Hackathon project**  
> A shared wallet for friends that collects subscription contributions through PayPal Sandbox and lets an owner safely delegate a bounded portion of the group pot to an AI-assisted booking workflow.

**Important:** This project is configured for a sandbox/demo environment. No real money should move in the default mock configuration. The interface labels mock, sandbox, and simulated operations explicitly.

## Problem we are solving

When friends share subscriptions, trips, or recurring expenses, one person often has to front the payment, chase everyone for their share, and manually coordinate purchases. This creates friction, unclear balances, and the risk that one person's contribution is used for another person's expense without clear accounting.

Forked Wallets addresses this by:
- Collecting members' shares into a group pot.
- Tracking contributions, reservations, spending, and unused funds in an auditable ledger.
- Helping coordinate recurring subscription cycles and unpaid shares.
- Allowing the group owner to describe a purchase in natural language and delegate a **limited, policy-controlled** budget to an AI-assisted task.
- Returning the unused portion of a task's reserved budget to the pot after a booking completes.

## What the project does

### Shared group pot and subscription collection
Members join a group and contribute their share toward a recurring subscription. The app tracks who has paid and the available pot balance. A cycle can pause if the group is short; the system does not silently increase other members' shares.

### AI-assisted task planning and booking
The owner can describe a task, for example: “Book a hotel in Austin for two nights under $200.” The planner converts the request into a structured, validated task. Search agents work against separate demo inventories. The first valid candidate claims the task through an atomic lock. A capped, single-use **mock payment card** is then issued for the selected merchant, and the booking is confirmed through the demo workflow.

### Fork, spend, and merge-back
The backend reserves a bounded amount from the pot (“fork”). Once the booking completes, the merchant payment is processed through PayPal Sandbox Payouts when enabled (otherwise it is explicitly simulated). Any unused amount is merged back into the group pot.

### Ledger and transparent attribution
The append-only ledger records contributions, reservations/forks, spending, refunds, and merge-backs. The app calculates the available amount from ledger entries rather than trusting an AI-generated balance. The UI identifies each action as `PAYPAL SANDBOX`, `MOCK PAYPAL`, or `SIMULATED`.

### Group coordination
The project includes recurring-cycle actions, reminders, group inbox notifications, consent and task controls, and scenarios for handling missed contributions and membership changes. Details and boundaries are described in the demo runbook and implementation documentation.

## How and why AI is used

AI is used for language understanding and task assistance—not as the authority over money.

- **Natural-language task planning:** The Planner agent interprets a user's booking request and returns a structured task specification.
- **Collections/reminder assistance:** AI can produce concise payment-shortfall reminders.
- **Parallel search agents:** Three agents race across demo hotel inventories to find a valid candidate.
- **Fallback behavior:** When an LLM is unavailable, misconfigured, or returns invalid output, the application uses deterministic mock/template behavior.

### AI safety boundaries
- AI does **not** choose the authoritative payment amount, recipient, spending cap, consent status, or payment state.
- LLM responses are treated as untrusted input and validated using Pydantic models; values are checked against backend/database rules.
- Agents can invoke only an allowlisted set of backend tools. Each tool re-checks policy.
- AI does not directly access PayPal, SQL, shell commands, or unrestricted HTTP.
- Database locking and idempotency enforce one task winner and prevent duplicate financial writes.

### AI tools/services used
The code supports pluggable LLM providers through a common interface:
- **Groq** (OpenAI-compatible Chat Completions endpoint; configured in this uploaded project variant)
- **OpenAI API**
- **Anthropic API**
- **Deterministic mock provider** (default, no external AI credentials needed)

To use Groq, configure `LLM_PROVIDER=groq`, provide `GROQ_API_KEY`, and set `LLM_MODEL` to a model available to your Groq account (the example `openai/gpt-oss-20b` is shown in `.env.example`/project documentation). The provider may fall back to deterministic mock behavior if configuration is absent or a request fails.

## PayPal APIs and their role

The project encapsulates payment operations behind a `PayPalProvider` interface with a mock implementation for local development.

| PayPal API / capability | How it is used |
|---|---|
| OAuth 2.0 (`/v1/oauth2/token`) | Obtains an access token for authenticated PayPal REST calls. |
| Orders v2 (`POST /v2/checkout/orders`) | Creates checkout orders for member contributions and, where configured, charges a saved vault token. |
| Orders v2 capture (`POST /v2/checkout/orders/{order_id}/capture`) | Captures an approved checkout order. Only a `COMPLETED` order/capture is treated as settled. |
| PayPal Vault | With payer consent and configuration enabled, stores a PayPal payment token for later auto-debit. |
| Payouts (`POST /v1/payments/payouts`) | Sends the demo merchant/hotel payment in PayPal Sandbox when payouts are enabled. |
| Captures refund (`POST /v2/payments/captures/{capture_id}/refund`) | Supports the refund workflow; verify the relevant API capability in the sandbox spike before enabling it. |

Payment requests are constructed by backend code using database-controlled amounts and references. PayPal `PayPal-Request-Id` idempotency keys are used for supported order/capture operations. PayPal Sandbox accounts and credentials are required only for the real-sandbox payment path; the default mock path runs without credentials.

**Sandbox setup note:** The project's existing setup guide specifies US PayPal Sandbox accounts and USD. Follow PayPal's developer dashboard requirements and the included `scripts/paypal_spike.py` checks. Vaulting and payouts remain disabled by default until their individual checks pass.

## Technology stack and developer tools

- **Backend:** Python, FastAPI, SQLAlchemy, Pydantic
- **Database:** PostgreSQL 16
- **Frontend:** React 18, Vite
- **Background processing:** Worker process and durable scheduled-job table
- **Payments:** PayPal REST APIs (Sandbox), provider abstraction and mock provider
- **AI:** Groq / OpenAI / Anthropic API integrations, deterministic fallback
- **Containerization:** Docker, Docker Compose
- **Testing:** pytest and project smoke scripts

## Run the project locally

### Prerequisites
- Docker installed
- Docker Compose plugin (`docker compose`)
- Git (to clone the public repository)

PayPal credentials and an LLM API key are optional for a local mock-mode demo.

### 1. Clone the repository
```bash
git clone https://github.com/Gazalshyam/share-subs
cd share-subs/
```

### 2. Configure environment
```bash
cp .env.example .env
```

The default values run the application with mock payments and deterministic AI. This is the recommended first run. Do not commit `.env` or any API keys.

### 3. Build and start services
```bash
docker compose up --build -d
```

The Compose stack starts:
- PostgreSQL database
- FastAPI application and built React frontend
- Background worker

### 4. Open the application
Visit: **http://localhost:8000**

Use the demo sign-in button (Asha, the group owner). Demo users use the password `demo1234` where prompted. Demo quick-login routes are available for local presentation:
```text
http://localhost:8000/api/v1/auth/demo-login/asha?next=/groups/1
http://localhost:8000/api/v1/auth/demo-login/ben?next=/groups/1
http://localhost:8000/api/v1/auth/demo-login/chloe?next=/groups/1
```
For multi-user steps, open separate browser profiles or private windows.

### 5. Reset the demo data
Mock mode:
```bash
docker compose exec api python -m app.demo_seed --reset --topup 7000
```
This demo top-up is mock-only. It seeds demo balances for the walkthrough.

Reset without top-up:
```bash
docker compose exec api python -m app.demo_seed --reset
```

### 6. Stop the application
```bash
docker compose down
```
To remove the database volume and wipe persisted data:
```bash
docker compose down -v
```

## Optional: configure Groq AI 

Edit `.env`:
```dotenv
LLM_PROVIDER=groq
LLM_MODEL=openai/gpt-oss-20b
GROQ_API_KEY=<your-groq-api-key>
```
Restart the stack:
```bash
docker compose up --build -d
```
Use a model identifier currently available to your Groq account. Keep the API key private. If Groq is not configured or its response fails validation, deterministic fallback behavior is used.

## Optional: PayPal Sandbox integration

1. In the PayPal Developer Dashboard, create the required Sandbox Business REST app and Personal buyer accounts. The project guide calls for US accounts and USD, including a separate Business account to represent the demo hotel.
2. Copy the Sandbox REST app Client ID and Secret into `.env`.
3. Set:
   ```dotenv
   PAYMENT_PROVIDER=paypal
   PAYPAL_BASE_URL=https://sandbox.paypal.com
   PAYPAL_CLIENT_ID=<sandbox-client-id>
   PAYPAL_CLIENT_SECRET=<sandbox-client-secret>
   ```
4. Run the API verification spike before enabling optional capabilities:
   ```bash
   python3 scripts/paypal_spike.py --env .env
   ```
5. Enable vaulting/payouts only after the relevant sandbox checks pass:
   ```dotenv
   PAYPAL_VAULT_ENABLED=false
   PAYPAL_PAYOUTS_ENABLED=false
   ```
   Change each to `true` only when validated for your Sandbox app/account.
6. Rebuild/restart:
   ```bash
   docker compose up --build -d
   ```

Sandbox setup and capability can depend on account eligibility. The mock mode is available as a reliable fallback for the demo.

## Tests and validation

Run unit tests (SQLite-backed test configuration; no external services needed):
```bash
docker compose run --rm --no-deps api pytest -q
```

For PostgreSQL-backed tests, use the setup instructions in the original project documentation. Smoke scripts are available, including:
```bash
bash scripts/smoke_cycle.sh
bash scripts/smoke_task.sh
bash scripts/smoke_enhancements.sh
bash scripts/smoke_pot_shares.sh
```
Run the scripts against a fresh/reset demo stack and consult their comments and `docs/demo-runbook.md` for prerequisites.

## Use cases and test cases covered

> **PLACEHOLDER — to be completed after the test cases/use cases are shared.**

We will document the submitted test cases here, including scenario, setup/preconditions, steps, expected outcome, and observed result. This section will be expanded based on the cases supplied by the project team.

| UC-0N | Use case | TestCase | Preconditions | ExpectedResults | Status | 
|---|---|---|---|---|---| 
| UC-01 | Sign in | TC-01.1 Demo sign-in as Asha, Ben or Chloe | `DEMO_MODE=true`, stack running | Signed HttpOnly session cookie set; `/auth/me` returns the user; dashboard loads | Pass (curl) | 
| UC-01 | Sign in | TC-01.2 Register and sign in with email and password | Email not yet registered | Password hashed with scrypt; 409 for a duplicate email; 422 for a password under 8 characters | Not run | 
| UC-02 | Create a group with a subscription | TC-02.1 Owner creates a group with a $15 StreamFlix plan | Signed in | Creator is OWNER; first cycle opens with renewal 7 days out; COLLECT, CHECK and RENEW jobs scheduled | Pass (pytest, via the demo seed) | 
| UC-03 | Invite and accept | TC-03.1 Owner invites by email; invitee accepts from the dashboard | Owner signed in; invitee has an account with that email | In-app INVITE item; after Accept the member is ACTIVE; untouched cycle re-split equally | Not run (steps in the "Create a group" walkthrough) | 
| UC-04 | Split the subscription | TC-04.1 Equal split with leftover cents ($10.00 or $10.01 between 3) | Group with 3 members | 334 / 333 / 333 and 334 / 334 / 333 cents; leftover cents go to the lowest member IDs | Pass (pytest) | 
| UC-04 | Split the subscription | TC-04.2 Custom split that does not sum to the price | Owner on the group page | Rejected (422); a split that sums exactly is saved | Pass (pytest, curl) | 
| UC-05 | Pay my share | TC-05.1 Member pays $5 through an approval order | Cycle open, mock PayPal | Approval link, then capture COMPLETED, then a CONTRIBUTION row with order and capture IDs, labelled MOCK PAYPAL | Pass (smoke) | 
| UC-05 | Pay my share | TC-05.2 Capture returns a status other than COMPLETED | Mock capture forced to return APPROVED | Attempt FAILED; no ledger row; nothing counted as collected | Pass (pytest) | 
| UC-05 | Pay my share | TC-05.3 Same idempotency key written twice | Existing ledger row | Exactly one row | Pass (pytest) | 
| UC-05 | Pay my share | TC-05.4 Real PayPal Sandbox approval and capture | US sandbox credentials in `.env`, `PAYMENT_PROVIDER=paypal` | Same as TC-05.1 with PAYPAL SANDBOX IDs | Not run | 
| UC-06 | Auto-debit | TC-06.1 COLLECT charges a saved token | Member on auto-debit with a saved mock token | Saved-token charge COMPLETED; share paid with no approval step | Pass (smoke) | 
| UC-06 | Auto-debit | TC-06.2 Forced failure falls back to approval | "Force next charge to fail" on | Attempt FAILED (SIMULATED, no PayPal call); "Pay my share" inbox item instead | Pass (smoke) | 
| UC-06 | Auto-debit | TC-06.3 Real vault token charge | Sandbox credentials, `PAYPAL_VAULT_ENABLED=true` | Charge COMPLETED from PayPal before auto-debit is shown as working | Not run | 
| UC-07 | Subscription cycle | TC-07.1 CHECK (D-1) with a shortfall | One share unpaid | Collections agent reminder with the shortfall to each member; mock template when the LLM is off (the one-per-day limit is in code but has no separate test) | Pass (smoke) | 
| UC-07 | Subscription cycle | TC-07.2 RENEW when fully collected | All shares paid | SIMULATED StreamFlix SPEND; next cycle opened with new shares | Pass (pytest, smoke) | 
| UC-07 | Subscription cycle | TC-07.3 RENEW when short | One share unpaid | Cycle and subscription PAUSED; nobody else's share raised; no SPEND | Pass (pytest) | 
| UC-08 | Cover a missed share (E2) | TC-08.1 Chloe covers Ben at D-1 | Ben unpaid after CHECK | Ben's share settled from the cycle snapshot; next cycle Asha $5, Ben $10, Chloe $0, with notes; back to equal after that | Pass (pytest, smoke) | 
| UC-08 | Cover a missed share (E2) | TC-08.2 Ben misses again in the very next cycle | Ben was covered last cycle | No cover offered (422); everyone told; RENEW pauses | Pass (pytest, smoke) | 
| UC-08 | Cover a missed share (E2) | TC-08.3 Share paid while a cover order is pending | Cover order created, then Ben pays | Cover CANCELLED, not captured, no ledger row | Pass (pytest) | 
| UC-08 | Cover a missed share (E2) | TC-08.4 Member tries to cover their own share | — | Rejected | Pass (pytest) | 
| UC-09 | Leave a group and vote (E1) | TC-09.1 Unpaid member leaves; the others vote AGREE | Ben unpaid | Decision OPEN; COLLECT charges nobody; after all agree, shares become 750 / 750 and COLLECT is queued | Pass (pytest, smoke) | 
| UC-09 | Leave a group and vote (E1) | TC-09.2 One PAUSE vote | Decision open | Subscription and cycle PAUSED; RENEW does nothing; owner can ask again with fresh votes | Pass (pytest) | 
| UC-09 | Leave a group and vote (E1) | TC-09.3 Paid member leaves | Ben paid this cycle | Current cycle renews unchanged; new split from the next cycle, after consent | Pass (pytest) | 
| UC-09 | Leave a group and vote (E1) | TC-09.4 Owner tries to leave; a leaver tries to vote | — | Both rejected | Pass (pytest) | 
| UC-10 | Whose money is in the pot (E3) | TC-10.1 Per-member parts after top-ups, shares, a renewal and a task | Several contributions and spends | Parts add up exactly to the pot's available and reserved numbers; shown as a pie on the group page | Pass (pytest, smoke); pie not viewed in a browser | 
| UC-10 | Whose money is in the pot (E3) | TC-10.2 A cover makes the debtor negative until repaid | Chloe covered Ben, renewal done | Ben −$5, Chloe +$5; both $0 after the repayment cycle | Pass (pytest) | 
| UC-11 | Refund on leaving (E3) | TC-11.1 Member with a surplus leaves | Ben has $70 above this cycle's $5 share | $70 refunded against his own captures (newest first, never above a capture's amount); REFUND rows with capture and refund IDs; the $5 stays for the renewal | Pass (pytest, smoke) | 
| UC-11 | Refund on leaving (E3) | TC-11.2 Member who owes money leaves | Ben's share was covered and not repaid | No refund and no charge; WRITE_OFF moves the $5 onto Chloe; everyone notified | Pass (pytest) | 
| UC-11 | Refund on leaving (E3) | TC-11.3 Leave while a task uses your money | Task awaiting consent or running | Rejected (422) with the task number | Pass (pytest, smoke) | 
| UC-11 | Refund on leaving (E3) | TC-11.4 Real PayPal capture refund | Sandbox credentials | Refund COMPLETED from PayPal before a REFUND row is written | Not run | 
| UC-12 | Plan an AI task | TC-12.1 "Plan with AI" on the hotel prompt | Owner; LLM mock or real | City, nights, guests and rating validated with Pydantic; budget entered by the owner, never by the AI | Pass (smoke, mock LLM) | 
| UC-12 | Plan an AI task | TC-12.2 Same with a real LLM (Groq) | `LLM_PROVIDER=groq`, key and model in `.env` | Plan source and agent reasons show the model label; template used on any LLM error | Reported by team | 
| UC-13 | Sub-pot and consent (E4) | TC-13.1 Task for Asha and Ben only | Each member has $100 | Budget above their $200 rejected; after Ben agrees the fork is 50/50; Chloe's part untouched | Pass (pytest, smoke) | 
| UC-13 | Sub-pot and consent (E4) | TC-13.2 One participant has less than an equal share | Asha $50, Ben $150, budget $200 | Fork Asha $50, Ben $150; the hotel price is split equally but capped at each person's fork | Pass (pytest) | 
| UC-13 | Sub-pot and consent (E4) | TC-13.3 A participant declines | Task awaiting consent | Member dropped; task cancelled with nothing forked if the rest cannot cover the budget | Pass (pytest) | 
| UC-13 | Sub-pot and consent (E4) | TC-13.4 Silence past the consent window | `CONSENT_TIMEOUT_SECONDS` elapsed, or owner clicks "end the wait" | Silent members OWNER_AUTHORIZED and notified; fork written once; race starts | Pass (pytest, smoke) | 
| UC-14 | Fork and agent race | TC-14.1 Budget above what the pot has available | Pot short | Rejected before any fork | Pass (pytest, smoke) | 
| UC-14 | Fork and agent race | TC-14.2 Two agents claim at the same moment | Task SEARCHING | Exactly one winner (atomic compare-and-set); the other is told the task is LOCKED | Pass (pytest) | 
| UC-14 | Fork and agent race | TC-14.3 Full race, hotel confirms | Consent given | Three agents search; first valid one wins; mock card (synthetic last 4, limit = price, DEMO_HOTEL, single use, 10 min) issued | Pass (smoke) | 
| UC-15 | Booking outcomes | TC-15.1 First hotel declines | Booking mode DECLINE_FIRST | Card closed; lock released; next valid candidate wins and books | Pass (smoke) | 
| UC-15 | Booking outcomes | TC-15.2 Booking outcome UNKNOWN | Booking mode UNKNOWN | NEEDS_REVIEW; lock and reservation kept; no second card; owner "did not book" merges everything back | Pass (pytest, smoke) | 
| UC-16 | Pay the hotel and merge back | TC-16.1 Confirmed booking | Price below budget | Payout to the demo hotel (MOCK PAYPAL in mock mode, SIMULATED if payouts are off); SPEND row; unused budget returns as MERGE_BACK split per participant | Pass (pytest, smoke) | 
| UC-16 | Pay the hotel and merge back | TC-16.2 Real PayPal payout | Sandbox credentials, `PAYPAL_PAYOUTS_ENABLED=true` | Payout batch ID from PayPal on the SPEND row | Not run | 
| UC-17 | Ledger and honest labels | TC-17.1 Ledger after the full demo | Moments 1 to 3 done | Every row labelled PAYPAL SANDBOX, MOCK PAYPAL or SIMULATED; available = collected − spent − refunded − reserved | Pass (pytest, smoke); ledger page not viewed in a browser | 
| UC-18 | Web UI walkthrough | TC-18.1 Click through `docs/demo-runbook.md`, including Demos E1 to E4 | Fresh stack in mock mode | Every screen behaves as the runbook says | Not run by Claude (Vite build only) |

## Functional demo and video

- **Local functional demo:** Follow the setup steps above; the project is runnable using Docker Compose.
- **YouTube demo video (under 3 minutes, public):** `[ADD PUBLIC YOUTUBE LINK]`

The demo video should show the running application on the target device, demonstrate real interaction (not a static mockup), and avoid unlicensed third-party trademarks or copyrighted music/material.



## Additional project documentation
- [`docs/demo-runbook.md`](docs/demo-runbook.md) — click-by-click presentation runbook
- [`docs/devpost.md`](docs/devpost.md) — hackathon project description
