"""
Agent Agora — Autonomous Trading Agent
Discovers the market intelligence service, pays per query, makes trading decisions.
Runs as a background loop, posting decisions to /api/agent/feed.

Usage:
    python agent.py                         # x402 mode via kpass current session
    AGORA_API_KEY=aa_... python agent.py    # paid subscription key mode
"""
from __future__ import annotations
import asyncio, json, logging, os, re, hashlib
from datetime import datetime, timezone
from json import JSONDecoder
from urllib.parse import quote
import httpx
from dotenv import load_dotenv

load_dotenv()

SERVICE_URL   = os.getenv("AGORA_URL", "https://agentagora.fly.dev")
API_KEY       = os.getenv("AGORA_API_KEY", "")
ADMIN_KEY     = os.getenv("ADMIN_KEY", "")
LOOP_INTERVAL = int(os.getenv("AGENT_INTERVAL", "300"))   # seconds between scans
MIN_CONFIDENCE = int(os.getenv("AGENT_MIN_CONFIDENCE", "45"))
SIGNAL_FILTER = os.getenv("AGENT_SIGNAL_FILTER", "ALL").upper()
MAX_OPPORTUNITIES = int(os.getenv("AGENT_MAX_OPPORTUNITIES", "25"))
RUN_ONCE = os.getenv("AGENT_RUN_ONCE", "0") == "1"
KPASS_TIMEOUT = int(os.getenv("AGENT_KPASS_TIMEOUT", "90"))
PAYMENT_MODE = os.getenv("AGENT_PAYMENT_MODE", "kpass").lower()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] agent: %(message)s")
log = logging.getLogger("agent")

# The agent settles paid intelligence on Kite and publishes signed decisions.
# It does not pretend to execute exchange trades; BUY/SELL/HOLD are live signals.
OBSERVED_POSITIONS: dict[str, float] = {}

def decision_hash(asset: str, action: str, ts: str) -> str:
    raw = f"{asset}:{action}:{ts}"
    return "0x" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def attestation_hash(decision: dict) -> str:
    payload = json.dumps(decision, sort_keys=True, separators=(",", ":"))
    return "0x" + hashlib.sha256(payload.encode()).hexdigest()[:40]


def extract_json(text: str):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    decoder = JSONDecoder()
    for i, ch in enumerate(text):
        if ch not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[i:])
            return value
        except json.JSONDecodeError:
            continue
    return None


def find_tx_hash(value) -> str:
    if isinstance(value, dict):
        for key in ("tx_hash", "txHash", "transactionHash", "transaction", "hash"):
            if value.get(key):
                return str(value[key])
        for nested in value.values():
            found = find_tx_hash(nested)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_tx_hash(item)
            if found:
                return found
    elif isinstance(value, str):
        match = re.search(r"0x[a-fA-F0-9]{32,64}", value)
        if match:
            return match.group(0)
    return ""


def normalize_kpass_payload(data) -> tuple[dict, dict]:
    """
    kpass versions wrap the paid HTTP response a few different ways. Return
    (response_json, proof_metadata) while being deliberately tolerant.
    """
    proof = {
        "payment_tx_hash": find_tx_hash(data),
        "payment_mode": "kpass-x402",
    }

    if isinstance(data, dict):
        for key in ("session_id", "sessionId", "request_id", "requestId"):
            if data.get(key):
                proof["kpass_session_id"] = data[key]
                break

        candidates = [
            data.get("body"),
            data.get("response_body"),
            data.get("responseBody"),
            data.get("data"),
            data.get("json"),
            (data.get("response") or {}).get("body") if isinstance(data.get("response"), dict) else data.get("response"),
        ]
        for candidate in candidates:
            if isinstance(candidate, dict):
                return candidate, proof
            if isinstance(candidate, str):
                parsed = extract_json(candidate)
                if isinstance(parsed, dict):
                    return parsed, proof

        # Some kpass builds return the endpoint body directly.
        if any(k in data for k in ("screener", "asset", "signals", "price_usd")):
            return data, proof

    if isinstance(data, str):
        parsed = extract_json(data)
        if isinstance(parsed, dict):
            return normalize_kpass_payload(parsed)

    return {}, proof


async def run_kpass_execute(url: str) -> tuple[dict, dict]:
    cmd = [
        "kpass", "agent:session", "execute",
        "--url", url,
        "--output", "json",
        "--no-interactive",
    ]
    log.info("kpass executing paid URL: %s", url)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=KPASS_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("kpass timed out while executing x402 payment")

    out = stdout.decode(errors="replace").strip()
    err = stderr.decode(errors="replace").strip()
    if proc.returncode != 0:
        raise RuntimeError(err or out or f"kpass exited with code {proc.returncode}")

    parsed = extract_json(out) or out
    return normalize_kpass_payload(parsed)


async def discover(client: httpx.AsyncClient) -> dict:
    r = await client.get(f"{SERVICE_URL}/api/discover")
    r.raise_for_status()
    return r.json()


async def screen_market(client: httpx.AsyncClient) -> list[dict]:
    url = f"{SERVICE_URL}/api/screener?signal={quote(SIGNAL_FILTER)}&min_confidence={MIN_CONFIDENCE}"
    if API_KEY:
        r = await client.get(url, headers={"X-API-Key": API_KEY}, timeout=30.0)
        r.raise_for_status()
        return r.json().get("screener", [])

    if ADMIN_KEY:
        r = await client.get(url, headers={"X-Admin-Key": ADMIN_KEY}, timeout=30.0)
        r.raise_for_status()
        return r.json().get("screener", [])

    if PAYMENT_MODE != "kpass":
        r = await client.get(url, timeout=30.0)
        if r.status_code == 402:
            log.warning("Payment required for screener — set AGORA_API_KEY or ADMIN_KEY")
            return []
        r.raise_for_status()
        return r.json().get("screener", [])

    payload, proof = await run_kpass_execute(url)
    rows = payload.get("screener", [])
    for row in rows:
        row["_payment"] = proof
    return rows


async def get_intel(client: httpx.AsyncClient, asset: str) -> dict:
    url = f"{SERVICE_URL}/api/market?asset={quote(asset)}"
    if API_KEY:
        r = await client.get(url, headers={"X-API-Key": API_KEY}, timeout=20.0)
        r.raise_for_status()
        payload = r.json()
        payload["_payment"] = {"payment_mode": "subscription", "api_key": API_KEY[:8] + "..."}
        return payload

    if ADMIN_KEY:
        r = await client.get(url, headers={"X-Admin-Key": ADMIN_KEY}, timeout=20.0)
        r.raise_for_status()
        payload = r.json()
        payload["_payment"] = {"payment_mode": "admin-worker"}
        return payload

    if PAYMENT_MODE != "kpass":
        r = await client.get(url, timeout=20.0)
        if r.status_code == 402:
            return {}
        r.raise_for_status()
        return r.json()

    payload, proof = await run_kpass_execute(url)
    payload["_payment"] = proof
    return payload


def make_decision(intel: dict, positions: dict) -> dict:
    action = intel.get("action", "HOLD")
    asset = intel["asset"]
    price = intel.get("price_usd") or 0
    confidence = intel.get("confidence", 0)
    held = positions.get(asset, 0.0)

    if action == "BUY":
        rationale = f"BUY signal for {asset} @ ${price:,.2f} — {intel.get('reasoning', '')}"
    elif action == "SELL":
        exposure = f" observed exposure {held:g} {asset};" if held else ""
        rationale = f"SELL signal for {asset} @ ${price:,.2f};{exposure} {intel.get('reasoning', '')}".replace("; ", " — ", 1)
    else:
        rationale = f"HOLD signal for {asset} — {intel.get('reasoning', '')}"

    ts = datetime.now(timezone.utc).isoformat()
    payment = intel.get("_payment") or {}
    decision = {
        "asset": asset,
        "action": action,
        "qty": 0,
        "price_usd": price,
        "confidence": confidence,
        "risk_level": intel.get("risk_level", 2),
        "stop_loss_pct": intel.get("stop_loss_pct"),
        "take_profit_pct": intel.get("take_profit_pct"),
        "rationale": rationale,
        "cash_delta_usdc": 0,
        "portfolio_units_after": round(held, 6),
        "fear_greed_index": intel.get("fear_greed_index"),
        "network": "eip155:2366",
        "settled_in": "USDC · Kite chain",
        "payment_mode": payment.get("payment_mode", "unknown"),
        "payment_tx_hash": payment.get("payment_tx_hash") or (payment.get("tx_hash") if isinstance(payment, dict) else None),
        "kpass_session_id": payment.get("kpass_session_id") if isinstance(payment, dict) else None,
        "decision_timestamp": ts,
    }
    decision["attestation_hash"] = attestation_hash(decision) or decision_hash(asset, action, ts)
    return decision


async def post_decision(client: httpx.AsyncClient, decision: dict) -> None:
    if not ADMIN_KEY and not API_KEY:
        log.warning("No ADMIN_KEY or AGORA_API_KEY; skipping feed post for %s %s", decision.get("action"), decision.get("asset"))
        return
    headers = {}
    if ADMIN_KEY:
        headers["X-Admin-Key"] = ADMIN_KEY
    elif API_KEY:
        headers["X-API-Key"] = API_KEY
    try:
        r = await client.post(
            f"{SERVICE_URL}/api/agent/feed",
            json=decision,
            headers=headers,
            timeout=10.0,
        )
        r.raise_for_status()
        log.info("Posted decision: %s %s (confidence %d%%)", decision["action"], decision["asset"], decision["confidence"])
    except Exception as e:
        log.error("Failed to post decision: %s", e)


async def run_loop() -> None:
    global OBSERVED_POSITIONS
    async with httpx.AsyncClient() as client:
        # Discover the service first
        try:
            svc = await discover(client)
            log.info("Discovered: %s — wallet %s", svc.get("service"), svc.get("wallet"))
        except Exception as e:
            log.warning("Discovery failed: %s", e)

        cycle = 0
        while True:
            cycle += 1
            log.info("=== Scan cycle %d ===", cycle)
            try:
                opportunities = await screen_market(client)
                log.info("Screener returned %d %s opportunities (min confidence %d%%)",
                         len(opportunities), SIGNAL_FILTER, MIN_CONFIDENCE)

                for opp in opportunities[:MAX_OPPORTUNITIES]:
                    asset = opp["asset"]
                    intel = await get_intel(client, asset)
                    if not intel:
                        continue
                    intel["action"] = opp["action"]
                    intel["confidence"] = opp["confidence"]
                    intel["risk_level"] = opp["risk_level"]
                    intel["stop_loss_pct"] = opp["stop_loss_pct"]
                    intel["take_profit_pct"] = opp["take_profit_pct"]
                    intel["reasoning"] = opp["reasoning"]

                    decision = make_decision(intel, OBSERVED_POSITIONS)
                    await post_decision(client, decision)
                    await asyncio.sleep(1)

                # Post cycle summary
                await post_decision(client, {
                    "asset": "AGENT",
                    "action": "SNAPSHOT",
                    "qty": 0,
                    "price_usd": None,
                    "confidence": 100,
                    "risk_level": 0,
                    "rationale": f"Cycle {cycle} complete — scanned {len(opportunities)} opportunities; all paid intelligence calls settle on Kite mainnet.",
                    "cash_delta_usdc": 0,
                    "portfolio_units_after": 0,
                    "fear_greed_index": None,
                    "network": "eip155:2366",
                    "attestation_hash": decision_hash("PORTFOLIO", "SNAPSHOT", datetime.now(timezone.utc).isoformat()),
                    "settled_in": "USDC · Kite chain",
                    "payment_mode": "none",
                })

            except Exception as e:
                log.error("Cycle error: %s", e)

            if RUN_ONCE:
                log.info("AGENT_RUN_ONCE=1 set — stopping after one cycle")
                return
            log.info("Sleeping %ds until next scan…", LOOP_INTERVAL)
            await asyncio.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    log.info("Agent Agora Autonomous Trading Agent starting")
    log.info("Service: %s | Interval: %ds | Signal: %s | Min confidence: %d%%",
             SERVICE_URL, LOOP_INTERVAL, SIGNAL_FILTER, MIN_CONFIDENCE)
    if not API_KEY:
        log.info("No AGORA_API_KEY set — using kpass x402 execution mode")
    asyncio.run(run_loop())
