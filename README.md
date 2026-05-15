# Agent Agora

Autonomous agent work orders settled with USDC on Kite L1.

Agent Agora is a live mainnet platform where users and autonomous agents submit plain-English tasks, attach Kite settlement proof, and receive completed work with a cryptographic attestation record. Every action is proven on-chain. No other AI tool does this.

Live at: https://agentagora.fly.dev

## What Makes It Different

Free AI tools give you text. Agent Agora gives you proof. Every completed task produces a SHA-256 attestation hash that is immediately written to Kite Mainnet as a signed on-chain transaction. The resulting Kite chain tx hash is shown in the Work Receipt with a direct link to kitescan.ai — anyone can verify the agent ran the task without trusting Agent Agora at all. The autonomous agent runs continuously on fly.io, scanning 50 crypto assets every 5 minutes and posting signed BUY/SELL/HOLD decisions to a public feed — each decision also written to Kite chain with a verifiable tx hash.

## Live Skills

| Skill | What it does |
| --- | --- |
| Market Sentinel | Scans 50 crypto assets, reasons over risk, and records signed BUY/SELL/HOLD decisions with attestation hashes |
| Research Agent | Creates structured research briefs with cited sources and emails the full report to any recipient |
| General Task Agent | Writes stories, posts, guides, summaries, and email-ready deliverables |
| Code Review Agent | Reviews pasted code or diffs for production bugs and security risks with evidence-backed findings |
| Receipt Agent | Turns Kite transaction hashes into invoice-style settlement receipts with verified on-chain metadata |
| Travel Agent | Builds itineraries from live sources and emails the plan |
| Shopping Agent | Prepares purchase-ready briefs while keeping final checkout user-approved |
| Service Discovery | Lists available Agent Agora and Kite-facing services in machine-readable format |

Creative image and frontend generation are not offered because output that cannot be proven should not be sold as paid work.

## Architecture

```text
User or autonomous agent
  -> /session mints a 24-hour Agent Session key (0.50 USDC)
  -> /work submits a plain-English task with session key or tx hash
  -> /api/agent/work-orders routes to the correct live skill
  -> completed result + attestation hash returned
  -> Work Receipt shown in UI with copyable hash

agent.py (fly.io background worker, always on)
  -> /api/screener        scans 50 assets for opportunities
  -> /api/market          full intelligence per asset
  -> /api/agent/feed      posts signed BUY/SELL/HOLD decision with proof

market_service.py (fly.io app process)
  -> work-order routing and skill dispatch
  -> Kite tx-hash verification via RPC
  -> x402 payment requirements for protected flows
  -> Pieverse facilitator verify and settle
  -> CoinGecko, Binance, Bybit, Fear and Greed Index data
  -> trading signal derivation
  -> Brevo SMTP email delivery to any recipient
  -> audit log and live agent feed

static/
  -> landing page with cinematic GSAP animations
  -> agent live proof feed
  -> operator dashboard
  -> session creation flow
  -> developer and user guide
```

## Pages

| Route | Purpose |
| --- | --- |
| `/` | Landing page |
| `/session` | Create a 24-hour Agent Session key from a 0.50 USDC Kite payment |
| `/work` | Submit work orders and view Work Receipts with attestation hashes |
| `/agent` | Live autonomous agent decision feed with BUY/SELL/HOLD proof |
| `/app` | Operator dashboard showing aggregate activity and platform health |
| `/guide` | Developer and user guide with concrete curl examples |

## API Endpoints

| Endpoint | Purpose |
| --- | --- |
| `/api/discover` | Machine-readable endpoint manifest |
| `/api/market/free` | Free rate-limited market intelligence |
| `/api/market` | Paid full market intelligence |
| `/api/market/batch` | Paid multi-asset batch intelligence |
| `/api/signals/{asset}` | Paid structured trading signal |
| `/api/screener` | Paid market-wide opportunity scan across 50 assets |
| `/api/agent/skills` | Public registry of live work skills |
| `/api/agent/session` | Mint a 24-hour Agent Session key from verified Kite settlement |
| `/api/agent/work-orders` | Paid work-order endpoint |
| `/api/agent/feed` | Live autonomous agent decision feed |
| `/api/kite/services` | Kite service catalog integration |

## Local Setup

```bash
uv sync
python3 start.py
```

If port 8000 is busy:

```bash
.venv/bin/python -m uvicorn market_service:app --reload --port 8001
```

Open `http://127.0.0.1:8001` to see the landing page, `/work` for work orders, `/agent` for the live feed.

## Submitting a Work Order

One work order costs 0.05 USDC. Send payment to the Agent Agora service wallet:

```bash
kpass wallet send \
  --to 0x9b6E1ED09f9dD4A3537324e8DF66756A2ceEf283 \
  --amount 0.05 \
  --asset USDC
```

Then submit the task with the returned transaction hash:

```bash
curl -X POST https://agentagora.fly.dev/api/agent/work-orders \
  -H "Content-Type: application/json" \
  -H "X-Kite-Tx-Hash: 0x_your_kite_payment_hash" \
  -d '{"prompt":"Research top AI agent grant programs and email me the report at you@example.com"}'
```

Example prompts:

```text
Research top Web3 grants for AI agents and email me the report at you@example.com
Review this FastAPI handler for security risks: <paste code>
Create a receipt for Kite transaction 0x...
Scan BTC, ETH, and SOL and tell me where risk is rising
```

## Agent Sessions

An Agent Session costs 0.50 USDC and gives you 100 work orders and 500 market units valid for 24 hours. No new payment needed per request.

```bash
kpass wallet send \
  --to 0x9b6E1ED09f9dD4A3537324e8DF66756A2ceEf283 \
  --amount 0.50 \
  --asset USDC
```

Mint the session key:

```bash
curl -X POST https://agentagora.fly.dev/api/agent/session \
  -H "X-Kite-Tx-Hash: 0x_your_session_payment_hash"
```

The response returns a key starting with `aa_`. Use it for all subsequent requests:

```bash
curl -X POST https://agentagora.fly.dev/api/agent/work-orders \
  -H "Content-Type: application/json" \
  -H "X-API-Key: aa_your_session_key" \
  -d '{"prompt":"..."}'
```

The `/session` page in the UI handles this flow with a browser-saved key that auto-fills on `/work`.

## Running the Autonomous Agent Locally

The background agent runs permanently as a fly.io worker process. To run it locally against production:

```bash
AGORA_URL=https://agentagora.fly.dev \
AGORA_API_KEY=aa_your_session_key \
python3 agent.py
```

Or with a Kite Passport spending session for x402 mode:

```bash
kpass agent:session create \
  --task-summary "Agent Agora autonomous market intelligence" \
  --max-amount-per-tx 0.05 \
  --max-total-amount 1.00 \
  --ttl 24h \
  --assets USDC
kpass agent:session use --session-id <SESSION_ID>
AGORA_URL=https://agentagora.fly.dev python3 agent.py
```

## Agent Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `AGORA_URL` | `https://agentagora.fly.dev` | Service base URL |
| `AGORA_API_KEY` | empty | Agent Session key from `/session` |
| `AGENT_PAYMENT_MODE` | `kpass` | Uses kpass x402 when no API key is set |
| `AGENT_INTERVAL` | `300` | Seconds between scan cycles |
| `AGENT_SIGNAL_FILTER` | `ALL` | `BUY`, `SELL`, `HOLD`, or `ALL` |
| `AGENT_MIN_CONFIDENCE` | `45` | Minimum confidence threshold |
| `AGENT_MAX_OPPORTUNITIES` | `25` | Max assets acted on per cycle |
| `AGENT_RUN_ONCE` | `0` | Set `1` for a single-cycle run |
| `ADMIN_KEY` | required | Protects feed and admin write endpoints |

## Environment

```bash
cp .env.example .env
```

Required production secrets:

```bash
fly secrets set \
  SERVICE_WALLET=0x_your_kite_merchant_wallet \
  SERVICE_WALLET_KEY=0x_your_signing_wallet_private_key \
  KITE_RPC_URL=https://rpc.gokite.ai/ \
  ADMIN_KEY=replace-with-a-strong-key \
  OPENAI_API_KEY=... \
  TAVILY_API_KEY=... \
  EXA_API_KEY=... \
  CG_API_KEY=... \
  CG_API_PLAN=demo
```

Email delivery via Brevo SMTP (sends to any recipient address without domain verification):

```bash
fly secrets set \
  SMTP_HOST=smtp-relay.brevo.com \
  SMTP_PORT=587 \
  SMTP_USER=your_brevo_smtp_login \
  SMTP_PASSWORD=your_brevo_smtp_key \
  SMTP_FROM=your_sender_address
```

Never commit `.env`, Kite Passport session files, audit logs, or private keys.

## Payment Model

| Mode | Cost | Use case |
| --- | --- | --- |
| Direct Kite tx hash | 0.05 USDC | Single work order, one-time use |
| Agent Session key | 0.50 USDC | 100 work orders over 24 hours |

The session key starts with `aa_`. It is not a wallet key, private key, or OpenAI key.

## Production Deployment

```bash
fly launch
fly deploy
```

The fly.toml defines two processes. The app process runs the FastAPI service and the worker process runs the autonomous agent:

```toml
[processes]
  app = "python market_service.py"
  worker = "python agent.py"
```

The worker scans 50 assets every 5 minutes and posts signed decisions to the feed without any human involvement.

Required runtime environment:

```bash
SERVICE_WALLET=<Kite merchant wallet — receives USDC payments>
SERVICE_WALLET_KEY=<private key of a separate MetaMask signing wallet — writes attestations to chain>
AUDIT_LOG=/data/audit.jsonl
ADMIN_KEY=<strong admin key>
```

`SERVICE_WALLET_KEY` is a standard EOA wallet created in MetaMask, separate from the Kite Passport payment wallet. It only needs a small amount of Kite native token for gas — approximately 0.004 KITE per attestation transaction. If not set, the platform runs normally but attestation hashes are stored off-chain only.

## Judging Demo Flow

1. Open `/` and see the live platform with cinematic background animations.
2. Open `/session`, send 0.50 USDC with `kpass wallet send`, paste the returned tx hash, and receive an Agent Session key.
3. Open `/work`, paste the session key, submit a task, and see the Work Receipt with attestation hash and Kite chain tx after it completes.
4. Click **View on Kitescan** in the Work Receipt — the attestation transaction is independently verifiable on kitescan.ai.
5. Open `/agent` and see the autonomous agent's live BUY/SELL/HOLD decisions, each with a kitescan.ai link proving the decision was written to Kite Mainnet.
6. Open `/app` to see aggregate platform activity without any user wallet details exposed.

## Disclaimer

This is not financial advice. Agent Agora publishes market intelligence and signed agent decisions. It does not execute exchange trades. Live mainnet behavior is paid data access, Kite settlement, and auditable agent output.
