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

| ID | Use case / test case | Preconditions | Expected result | Status |
|---|---|---|---|---|
| UC-01 | `[PLACEHOLDER]` | `[PLACEHOLDER]` | `[PLACEHOLDER]` | `[PLACEHOLDER]` |

## Functional demo and video

- **Local functional demo:** Follow the setup steps above; the project is runnable using Docker Compose.
- **YouTube demo video (under 3 minutes, public):** `[ADD PUBLIC YOUTUBE LINK]`

The demo video should show the running application on the target device, demonstrate real interaction (not a static mockup), and avoid unlicensed third-party trademarks or copyrighted music/material.



## Additional project documentation
- [`docs/demo-runbook.md`](docs/demo-runbook.md) — click-by-click presentation runbook
- [`docs/devpost.md`](docs/devpost.md) — hackathon project description
