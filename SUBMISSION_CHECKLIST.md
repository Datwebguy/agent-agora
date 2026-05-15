# Public Submission Checklist

## Must Show In Demo

- [ ] Public mainnet production URL loads.
- [ ] `/work` shows the live agent work-order UI.
- [ ] `/api/discover` returns paid endpoint metadata, wallet, network, and pricing.
- [ ] A fresh Kite tx hash successfully unlocks one `/api/agent/work-orders` action.
- [ ] Reusing the same Kite tx hash is rejected.
- [ ] A subscription key can run repeated tasks until quota/expiry.
- [ ] `agent.py` runs with minimal human involvement after session approval.
- [ ] `/agent` shows a fresh decision with:
  - [ ] action: BUY, HOLD, SELL, or SNAPSHOT
  - [ ] rationale
  - [ ] confidence/risk
  - [ ] Kite network
  - [ ] payment tx hash when paid through kpass
  - [ ] decision attestation hash
- [ ] `/app` shows paid calls, revenue, payer wallets, and audit data.
- [ ] README can reproduce the production run.

## Current Project Status

- [x] Paid API service exists.
- [x] x402 payment requirements implemented.
- [x] Pieverse facilitator verify/settle implemented.
- [x] `/api/screener` exists.
- [x] Autonomous `agent.py` exists.
- [x] Agent can call paid endpoints through kpass when no API key is set.
- [x] `/agent` live feed exists.
- [x] Dashboard exists.
- [x] Docker/Fly deployment config exists.
- [x] README added.

## Strongest Review Story

Agent Agora is a two-sided agentic commerce system:

1. It is a market intelligence service that any AI agent can discover and pay for.
2. It includes its own autonomous trading agent that consumes the paid service, settles on Kite, and publishes proof.

This makes the platform concrete: an agent pays another service, gets data, makes a decision, and leaves an auditable trail.

## Remaining Polish Before Submission

- [x] Deploy latest code to production.
- [ ] Replace default `ADMIN_KEY` in production.
- [x] Confirm service wallet has correct Kite merchant address.
- [ ] Run one real kpass paid call in production and capture tx hash.
- [ ] Record a short video showing `/agent` before and after the autonomous run.
- [ ] Add production URL to README after deploy.
