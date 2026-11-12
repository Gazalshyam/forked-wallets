# Forked Wallets — AI-Powered Shared Pot

> **PayPal AI Hackathon Project**
>
> Forked Wallets is an AI-powered shared wallet that helps friends collect subscription contributions through PayPal Sandbox and safely delegate a limited portion of their group funds to AI-assisted booking workflows.

**Important:** This project is configured for a sandbox/demo environment. No real money should move in the default mock configuration. Mock, sandbox, and simulated operations are explicitly identified throughout the application.

---

## Problem We Are Solving

When friends share subscriptions, trips, or recurring expenses, one person often has to pay upfront, collect contributions from everyone, and manually coordinate purchases. This creates unnecessary friction, unclear balances, and the risk of one person's contribution being used for another person's expense without proper accounting.

Forked Wallets addresses these challenges by:

- Collecting members' contributions into a shared group pot.
- Tracking contributions, reservations, spending, refunds, and unused funds through an auditable ledger.
- Coordinating recurring subscription cycles and unpaid contributions.
- Allowing the group owner to describe a purchase in natural language and delegate a limited, policy-controlled budget to an AI-assisted task.
- Returning unused portions of reserved task budgets to the group pot after a booking is completed.

## What the Project Does

### 1. Shared Group Pot and Subscription Collection

Members can join a group and contribute their share toward a recurring subscription. The application tracks individual payments and the available group balance.

If the group does not collect the full subscription amount, the cycle can be paused. The system never silently increases other members' contributions to cover an unpaid share.

### 2. AI-Assisted Task Planning and Booking

The group owner can describe a task in natural language, for example:

"Book a hotel in Austin for two nights under $200."

The AI planner converts the request into a structured and validated task specification.

Multiple search agents work against separate demo hotel inventories. The first valid candidate claims the task through an atomic locking mechanism.

A capped, single-use mock payment card is then issued for the selected merchant, and the booking is confirmed through the demo workflow.

### 3. Fork, Spend, and Merge-Back

The backend reserves a bounded amount from the group pot for a task. This operation is called a **fork**.

Once the booking is completed, the merchant payment is processed through PayPal Sandbox Payouts when enabled. Otherwise, the payment is explicitly simulated.

Any unused portion of the reserved budget is returned to the group pot through a **merge-back** operation.

### 4. Ledger and Transparent Attribution

The application maintains an append-only ledger that records:

- Member contributions.
- Budget reservations and forks.
- Merchant spending.
- Refunds.
- Unused budget merge-backs.

The available balance is calculated from ledger entries rather than relying on an AI-generated balance.

Every financial operation is clearly identified as one of:

- `PAYPAL SANDBOX`
- `MOCK PAYPAL`
- `SIMULATED`

### 5. Group Coordination

The project also supports recurring-cycle actions, reminders, group inbox notifications, member consent, task controls, and scenarios involving missed contributions and membership changes.

These workflows are documented in the demo runbook and implementation documentation.

---

## How and Why AI Is Used

AI is used for language understanding and task assistance, not as the authority over financial operations.

The application uses AI in the following areas:

- **Natural-language task planning:** The Planner agent interprets booking requests and generates structured task specifications.
- **Collection and reminder assistance:** AI generates concise reminders when subscription contributions are insufficient.
- **Parallel search agents:** Three agents search separate demo hotel inventories to identify valid booking candidates.
- **Fallback behavior:** If an LLM is unavailable, misconfigured, or returns invalid output, the application uses deterministic mock/template behavior.

### AI Safety Boundaries

Financial decisions remain under backend control.

- AI does not determine authoritative payment amounts, recipients, spending limits, consent status, or payment states.
- LLM responses are treated as untrusted input and validated using Pydantic models.
- All values are checked against backend and database rules.
- Agents can invoke only an allowlisted set of backend tools.
- Every tool independently rechecks the applicable policies.
- AI does not directly access PayPal, SQL, shell commands, or unrestricted HTTP.
- Database locking and idempotency prevent multiple task winners and duplicate financial writes.

### AI Tools and Services Used

The application supports pluggable LLM providers through a common interface:

- **Groq:** OpenAI-compatible Chat Completions endpoint, configured in this project variant.
- **OpenAI API**
- **Anthropic API**
- **Deterministic Mock Provider:** Default option that works without external AI credentials.

To use Groq, configure `LLM_PROVIDER=groq`, provide `GROQ_API_KEY`, and set `LLM_MODEL` to a model available to your Groq account.

The example model identifier is `openai/gpt-oss-20b`.

If Groq is not configured or a request fails, the provider can fall back to deterministic mock behavior.

---

## PayPal APIs and Their Role

The application encapsulates payment operations behind a `PayPalProvider` interface, with a mock implementation available for local development.

| PayPal API / Capability | How It Is Used |
|---|---|
| OAuth 2.0 (`/v1/oauth2/token`) | Obtains an access token for authenticated PayPal REST API calls. |
| Orders v2 (`POST /v2/checkout/orders`) | Creates checkout orders for member contributions and, where configured, charges a saved vault token. |
| Orders v2 Capture (`POST /v2/checkout/orders/{order_id}/capture`) | Captures an approved checkout order. Only a `COMPLETED` order/capture is treated as settled. |
| PayPal Vault | With payer consent and configuration enabled, stores a PayPal payment token for later auto-debit. |
| Payouts (`POST /v1/payments/payouts`) | Sends the demo merchant/hotel payment through PayPal Sandbox when payouts are enabled. |
| Captures Refund (`POST /v2/payments/captures/{capture_id}/refund`) | Supports the refund workflow. The relevant API capability must be verified in the sandbox spike before enabling it. |

Payment requests are constructed by backend code using database-controlled amounts and references.

PayPal `PayPal-Request-Id` idempotency keys are used for supported order and capture operations.

PayPal Sandbox accounts and credentials are required only for the real-sandbox payment path. The default mock path runs without external payment credentials.

### Sandbox Setup Notes

The existing project setup guide specifies US PayPal Sandbox accounts and USD.

Follow the PayPal Developer Dashboard requirements and run the included `scripts/paypal_spike.py` checks.

Vaulting and payouts remain disabled by default until their individual sandbox checks pass.

---

## Technology Stack and Developer Tools

| Component | Technology |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy, Pydantic |
| Database | PostgreSQL 16 |
| Frontend | React 18, Vite |
| Background Processing | Worker process and durable scheduled-job table |
| Payments | PayPal REST APIs (Sandbox), provider abstraction and mock provider |
| AI Integration | Groq, OpenAI, Anthropic APIs and deterministic fallback |
| Containerization | Docker, Docker Compose |
| Testing | pytest and project smoke scripts |

## System Architecture

The application follows a backend-driven architecture in which the frontend, backend, database, background workers, AI agents, and payment provider work together.

1. **Frontend:** React communicates with the FastAPI backend to provide group management, contribution tracking, task planning, and booking workflows.
2. **Backend:** FastAPI handles authentication, group management, subscription cycles, task orchestration, financial operations, and API requests.
3. **Database:** PostgreSQL stores users, groups, contributions, reservations, scheduled jobs, and ledger entries.
4. **Background Workers:** The worker process handles scheduled subscription cycles, reminders, and recurring jobs using the durable scheduled-job table.
5. **AI Agents:** The planner interprets natural-language requests, while parallel search agents identify valid candidates through controlled backend tools.
6. **Payment Provider:** PayPal operations are abstracted behind a provider interface, allowing the application to support mock payments and PayPal Sandbox operations.

The backend remains responsible for financial validation, authorization, spending limits, consent, and ledger updates.

---

## Run the Project Locally

### Prerequisites

Before starting, make sure you have:

- Docker installed.
- Docker Compose plugin (`docker compose`).
- Git installed to clone the repository.

PayPal credentials and an LLM API key are optional for a local mock-mode demo.

### 1. Clone the Repository

```bash
git clone https://github.com/Gazalshyam/share-subs
cd share-subs/
```

### 2. Configure Environment

Create your local environment file:

```bash
cp .env.example .env
```

The default configuration runs the application using mock payments and deterministic AI.

This is the recommended configuration for the first run.

Do not commit `.env` files or expose API keys in the repository.

### 3. Build and Start Services

```bash
docker compose up --build -d
```

The Docker Compose stack starts:

- PostgreSQL database.
- FastAPI application and built React frontend.
- Background worker.

### 4. Open the Application

Visit:

**http://localhost:8000**

Use the demo sign-in button to log in as Asha, the group owner.

Demo users use the password `demo1234` where prompted.

The following demo quick-login routes are available for local presentation:

```text
http://localhost:8000/api/v1/auth/demo-login/asha?next=/groups/1
http://localhost:8000/api/v1/auth/demo-login/ben?next=/groups/1
http://localhost:8000/api/v1/auth/demo-login/chloe?next=/groups/1
```

For multi-user workflows, open separate browser profiles or private windows.

### 5. Reset the Demo Data

To reset mock-mode demo data and seed balances:

```bash
docker compose exec api python -m app.demo_seed --reset --topup 7000
```

**Note:** The demo top-up is mock-only and seeds demo balances for the walkthrough.

To reset without adding a top-up:

```bash
docker compose exec api python -m app.demo_seed --reset
```

### 6. Stop the Application

```bash
docker compose down
```

To remove the database volume and wipe persisted data:

```bash
docker compose down -v
```

---

## Optional: Configure Groq AI

To enable Groq-based AI responses, edit your `.env` file:

```dotenv
LLM_PROVIDER=groq
LLM_MODEL=openai/gpt-oss-20b
GROQ_API_KEY=<your-groq-api-key>
```

Restart the application:

```bash
docker compose up --build -d
```

Use a model identifier currently available to your Groq account.

Keep your API key private.

If Groq is not configured or its response fails validation, the application uses deterministic fallback behavior.

---

## Optional: PayPal Sandbox Integration

To enable PayPal Sandbox integration:

1. Open the PayPal Developer Dashboard.
2. Create the required Sandbox Business REST app and Personal buyer accounts.
3. Follow the project guide for US Sandbox accounts and USD, including a separate Business account to represent the demo hotel.
4. Copy the Sandbox REST app Client ID and Secret into your `.env` file.

Configure the following environment variables:

```dotenv
PAYMENT_PROVIDER=paypal
PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com
PAYPAL_CLIENT_ID=<sandbox-client-id>
PAYPAL_CLIENT_SECRET=<sandbox-client-secret>
```

5. Run the API verification spike before enabling optional capabilities:

```bash
python3 scripts/paypal_spike.py --env .env
```

6. Enable vaulting and payouts only after their relevant Sandbox checks pass:

```dotenv
PAYPAL_VAULT_ENABLED=false
PAYPAL_PAYOUTS_ENABLED=false
```

Change each value to `true` only after validating the corresponding capability for your Sandbox app and account.

7. Rebuild and restart the application:

```bash
docker compose up --build -d
```

**Important:** Sandbox setup and available capabilities can depend on account eligibility. Mock mode remains available as a fallback for the demo.

---

## Tests and Validation

The project includes unit tests and smoke scripts covering authentication, subscription management, contribution collection, payment workflows, AI-assisted tasks, booking outcomes, and ledger operations.

### Run Unit Tests

Run the unit tests using the SQLite-backed test configuration. External services are not required:

```bash
docker compose run --rm --no-deps api pytest -q
```

### PostgreSQL-Backed Tests

For PostgreSQL-backed testing, follow the setup instructions in the original project documentation.

### Available Smoke Tests

The following smoke scripts are available:

```bash
bash scripts/smoke_cycle.sh
bash scripts/smoke_task.sh
bash scripts/smoke_enhancements.sh
bash scripts/smoke_pot_shares.sh
```

Run the scripts against a fresh or reset demo stack.

Refer to the script comments and `docs/demo-runbook.md` for their respective prerequisites.

---

## Use Cases and Test Cases Covered

The following table documents the project's use cases, test scenarios, preconditions, expected results, and current execution status.

The statuses distinguish between automated tests, smoke tests, manual checks, and scenarios that have not yet been executed.

| UC | Use Case | Test Case | Preconditions | Expected Results | Status |
|---|---|---|---|---|---|
| UC-01 | Sign in | TC-01.1 Demo sign-in as Asha, Ben or Chloe | `DEMO_MODE=true`, stack running | Signed HttpOnly session cookie set; `/auth/me` returns the user; dashboard loads | Pass (curl) |
| UC-01 | Sign in | TC-01.2 Register and sign in with email and password | Email not yet registered | Password hashed with scrypt; 409 for duplicate email; 422 for password under 8 characters | Not run |
| UC-02 | Create a group with a subscription | TC-02.1 Owner creates a group with a $15 StreamFlix plan | Signed in | Creator is OWNER; first cycle opens with renewal 7 days out; COLLECT, CHECK and RENEW jobs scheduled | Pass (pytest, via demo seed) |
| UC-03 | Invite and accept | TC-03.1 Owner invites by email; invitee accepts from dashboard | Owner signed in; invitee has an account with that email | In-app INVITE item; after Accept, member is ACTIVE; untouched cycle is re-split equally | Not run (walkthrough documented) |
| UC-04 | Split the subscription | TC-04.1 Equal split with leftover cents ($10.00 or $10.01 between 3) | Group with 3 members | 334 / 333 / 333 and 334 / 334 / 333 cents; leftover cents go to the lowest member IDs | Pass (pytest) |
| UC-04 | Split the subscription | TC-04.2 Custom split that does not sum to the price | Owner on group page | Rejected (422); a split that sums exactly is saved | Pass (pytest, curl) |
| UC-05 | Pay my share | TC-05.1 Member pays $5 through an approval order | Cycle open, mock PayPal | Approval link, then capture COMPLETED, then a CONTRIBUTION row with order and capture IDs, labelled MOCK PAYPAL | Pass (smoke) |
| UC-05 | Pay my share | TC-05.2 Capture returns a status other than COMPLETED | Mock capture forced to return APPROVED | Attempt FAILED; no ledger row; nothing counted as collected | Pass (pytest) |
| UC-05 | Pay my share | TC-05.3 Same idempotency key written twice | Existing ledger row | Exactly one row | Pass (pytest) |
| UC-05 | Pay my share | TC-05.4 Real PayPal Sandbox approval and capture | US Sandbox credentials in `.env`, `PAYMENT_PROVIDER=paypal` | Same as TC-05.1 with PAYPAL SANDBOX IDs | Not run |
| UC-06 | Auto-debit | TC-06.1 COLLECT charges a saved token | Member on auto-debit with a saved mock token | Saved-token charge COMPLETED; share paid with no approval step | Pass (smoke) |
| UC-06 | Auto-debit | TC-06.2 Forced failure falls back to approval | "Force next charge to fail" enabled | Attempt FAILED (SIMULATED, no PayPal call); "Pay my share" inbox item instead | Pass (smoke) |
| UC-06 | Auto-debit | TC-06.3 Real vault token charge | Sandbox credentials, `PAYPAL_VAULT_ENABLED=true` | Charge COMPLETED from PayPal before auto-debit is shown as working | Not run |
| UC-07 | Subscription cycle | TC-07.1 CHECK (D-1) with a shortfall | One share unpaid | Collections agent reminder with the shortfall to each member; mock template when LLM is off (one-per-day limit is in code but has no separate test) | Pass (smoke) |
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
| UC-10 | Whose money is in the pot (E3) | TC-10.1 Per-member parts after top-ups, shares, a renewal and a task | Several contributions and spends | Parts add up exactly to the pot's available and reserved numbers; shown as a pie on the group page | Pass (pytest, smoke); pie not viewed in browser |
| UC-10 | Whose money is in the pot (E3) | TC-10.2 A cover makes the debtor negative until repaid | Chloe covered Ben, renewal done | Ben −$5, Chloe +$5; both $0 after the repayment cycle | Pass (pytest) |
| UC-11 | Refund on leaving (E3) | TC-11.1 Member with a surplus leaves | Ben has $70 above this cycle's $5 share | $70 refunded against his own captures (newest first, never above a capture's amount); REFUND rows with capture and refund IDs; the $5 stays for renewal | Pass (pytest, smoke) |
| UC-11 | Refund on leaving (E3) | TC-11.2 Member who owes money leaves | Ben's share was covered and not repaid | No refund and no charge; WRITE_OFF moves the $5 onto Chloe; everyone notified | Pass (pytest) |
| UC-11 | Refund on leaving (E3) | TC-11.3 Leave while a task uses your money | Task awaiting consent or running | Rejected (422) with the task number | Pass (pytest, smoke) |
| UC-11 | Refund on leaving (E3) | TC-11.4 Real PayPal capture refund | Sandbox credentials | Refund COMPLETED from PayPal before a REFUND row is written | Not run |
| UC-12 | Plan an AI task | TC-12.1 "Plan with AI" on the hotel prompt | Owner; LLM mock or real | City, nights, guests and rating validated with Pydantic; budget entered by owner, never by AI | Pass (smoke, mock LLM) |
| UC-12 | Plan an AI task | TC-12.2 Same with a real LLM (Groq) | `LLM_PROVIDER=groq`, key and model in `.env` | Plan source and agent reasons show the model label; template used on any LLM error | Reported by team (not independently verified) |
| UC-13 | Sub-pot and consent (E4) | TC-13.1 Task for Asha and Ben only | Each member has $100 | Budget above their $200 rejected; after Ben agrees, the fork is 50/50; Chloe's part untouched | Pass (pytest, smoke) |
| UC-13 | Sub-pot and consent (E4) | TC-13.2 One participant has less than an equal share | Asha $50, Ben $150, budget $200 | Fork Asha $50, Ben $150; hotel price is split equally but capped at each person's fork | Pass (pytest) |
| UC-13 | Sub-pot and consent (E4) | TC-13.3 A participant declines | Task awaiting consent | Member dropped; task cancelled with nothing forked if the rest cannot cover the budget | Pass (pytest) |
| UC-13 | Sub-pot and consent (E4) | TC-13.4 Silence past the consent window | `CONSENT_TIMEOUT_SECONDS` elapsed, or owner clicks "end the wait" | Silent members OWNER_AUTHORIZED and notified; fork written once; race starts | Pass (pytest, smoke) |
| UC-14 | Fork and agent race | TC-14.1 Budget above what the pot has available | Pot short | Rejected before any fork | Pass (pytest, smoke) |
| UC-14 | Fork and agent race | TC-14.2 Two agents claim at the same moment | Task SEARCHING | Exactly one winner (atomic compare-and-set); the other is told the task is LOCKED | Pass (pytest) |
| UC-14 | Fork and agent race | TC-14.3 Full race, hotel confirms | Consent given | Three agents search; first valid one wins; mock card (synthetic last 4, limit = price, DEMO_HOTEL, single use, 10 min) issued | Pass (smoke) |
| UC-15 | Booking outcomes | TC-15.1 First hotel declines | Booking mode DECLINE_FIRST | Card closed; lock released; next valid candidate wins and books | Pass (smoke) |
| UC-15 | Booking outcomes | TC-15.2 Booking outcome UNKNOWN | Booking mode UNKNOWN | NEEDS_REVIEW; lock and reservation kept; no second card; owner "did not book" merges everything back | Pass (pytest, smoke) |
| UC-16 | Pay the hotel and merge back | TC-16.1 Confirmed booking | Price below budget | Payout to demo hotel (MOCK PAYPAL in mock mode, SIMULATED if payouts are off); SPEND row; unused budget returns as MERGE_BACK split per participant | Pass (pytest, smoke) |
| UC-16 | Pay the hotel and merge back | TC-16.2 Real PayPal payout | Sandbox credentials, `PAYPAL_PAYOUTS_ENABLED=true` | Payout batch ID from PayPal on the SPEND row | Not run |
| UC-17 | Ledger and honest labels | TC-17.1 Ledger after the full demo | Moments 1 to 3 done | Every row labelled PAYPAL SANDBOX, MOCK PAYPAL or SIMULATED; available = collected − spent − refunded − reserved | Pass (pytest, smoke); ledger page not viewed in browser |
| UC-18 | Web UI walkthrough | TC-18.1 Click through `docs/demo-runbook.md`, including Demos E1 to E4 | Fresh stack in mock mode | Every screen behaves as the runbook says | Not run (manual UI walkthrough pending) |

---

## Functional Demo and Video

### Local Functional Demo

The application can be run locally using Docker Compose.

Follow the setup instructions in the **Run the Project Locally** section to start the application and explore the demo workflows.

### YouTube Demo Video

**Public demo video (under 3 minutes):**

`[ADD PUBLIC YOUTUBE LINK]`

The demo video should:

- Show the running application on the target device.
- Demonstrate actual user interactions rather than static mockups.
- Cover the main shared wallet and AI-assisted booking workflows.
- Clearly identify mock, sandbox, and simulated payment operations.
- Avoid unlicensed third-party trademarks or copyrighted music/material.

---

## Additional Project Documentation

The following documents provide more information about the project:

- [`docs/demo-runbook.md`](docs/demo-runbook.md) — Click-by-click presentation runbook covering the demo workflows.
- [`docs/devpost.md`](docs/devpost.md) — Hackathon project description and supporting information.

---

**Forked Wallets — Shared contributions, controlled spending, and AI-assisted booking with transparent accounting.**
