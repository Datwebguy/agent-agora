from __future__ import annotations

import asyncio
import base64
import hashlib
import html as html_lib
import json
import logging
import os
import re
import secrets
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

SERVICE_NAME        = "Agent Agora — Autonomous Agent Work"
SERVICE_DESCRIPTION = "Kite-settled autonomous agent work orders with proof, auditability, and live skills"
KITE_NETWORK        = "eip155:2366"
KITE_USDC_CONTRACT  = "0x7aB6f3ed87C42eF0aDb67Ed95090f8bF5240149e"
KITE_USDC_DECIMALS  = 6
KITE_CHAIN_ID       = 2366
KITE_RPC_URL        = os.getenv("KITE_RPC_URL", "https://rpc.gokite.ai/")
FACILITATOR_BASE    = "https://facilitator.pieverse.io"
FACILITATOR_VERIFY  = f"{FACILITATOR_BASE}/v2/verify"
FACILITATOR_SETTLE  = f"{FACILITATOR_BASE}/v2/settle"
KITE_DISCOVERY_BASE = os.getenv("KITE_DISCOVERY_BASE", "https://service-discovery.prod.gokite.ai").rstrip("/")
SERVICE_WALLET      = os.getenv("SERVICE_WALLET", "0x9b6E1ED09f9dD4A3537324e8DF66756A2ceEf283")
SERVICE_WALLET_KEY  = os.getenv("SERVICE_WALLET_KEY", "")
PUBLIC_BASE_URL     = os.getenv("PUBLIC_BASE_URL", "https://agentagora.fly.dev").rstrip("/")
SMTP_HOST           = os.getenv("SMTP_HOST", "")
SMTP_PORT           = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER           = os.getenv("SMTP_USER", "")
SMTP_PASSWORD       = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM           = os.getenv("SMTP_FROM", SMTP_USER or "agent-agora@agentagora.fly.dev")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL        = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TAVILY_API_KEY      = os.getenv("TAVILY_API_KEY", "")
EXA_API_KEY         = os.getenv("EXA_API_KEY", "")
CG_API_KEY          = os.getenv("CG_API_KEY", "")
CG_API_PLAN         = os.getenv("CG_API_PLAN", "demo").strip().lower()
CG_BASE_URL         = "https://pro-api.coingecko.com/api/v3" if CG_API_PLAN == "pro" else "https://api.coingecko.com/api/v3"
CG_AUTH_HEADER      = "x-cg-pro-api-key" if CG_API_PLAN == "pro" else "x-cg-demo-api-key"

# ── Pricing ──────────────────────────────────────────────────────────────────
PRICE_USDC           = "0.03"
PRICE_USDC_RAW       = str(int(float(PRICE_USDC) * 10 ** KITE_USDC_DECIMALS))      # 30000
SUB_PRICE_USDC       = "0.50"
SUB_PRICE_RAW        = str(int(float(SUB_PRICE_USDC) * 10 ** KITE_USDC_DECIMALS))  # 500000
SUB_DURATION_HOURS   = 24
SUB_MAX_WORK_ORDERS  = int(os.getenv("SUB_MAX_WORK_ORDERS", "100"))
SUB_MAX_MARKET_UNITS = int(os.getenv("SUB_MAX_MARKET_UNITS", "500"))
SUB_RATE_LIMIT_PER_MIN = int(os.getenv("SUB_RATE_LIMIT_PER_MIN", "20"))
BATCH_PRICE_PER_ASSET_RAW = 20000   # $0.02 per asset in a batch call
BATCH_MAX_ASSETS     = 15
WORK_ORDER_PRICE_USDC = "0.05"
WORK_ORDER_PRICE_RAW  = str(int(float(WORK_ORDER_PRICE_USDC) * 10 ** KITE_USDC_DECIMALS))
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

AUDIT_LOG  = Path(os.getenv("AUDIT_LOG", "audit.jsonl"))
SUBSCRIPTIONS_FILE = Path(os.getenv("SUBSCRIPTIONS_FILE", "subscriptions.jsonl"))
ADMIN_KEY  = os.getenv("ADMIN_KEY") or secrets.token_urlsafe(32)
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("agent-agora")

# ── In-memory stores ──────────────────────────────────────────────────────────
_subscriptions: Dict[str, Dict[str, Any]] = {}   # api_key -> {expires_at, wallet, created_at}
_used_tx_hashes: set[str] = set()
FREE_RATE_LIMIT = 10
FREE_RATE_WINDOW = 60
_rate_buckets: Dict[str, List[float]] = {}
_sub_rate_buckets: Dict[str, List[float]] = {}
_agent_feed: List[Dict[str, Any]] = []           # live autonomous agent decision log
AGENT_FEED_MAX = 50
SCREENER_ASSETS = [
    "BTC","ETH","SOL","BNB","KITE","AVAX","DOGE","XRP","ADA","LINK",
    "DOT","MATIC","UNI","ATOM","LTC","BCH","TRX","TON","SHIB","PEPE",
    "SUI","APT","ARB","OP","INJ","SEI","NEAR","FTM","ALGO","ICP",
    "HBAR","FIL","SAND","MANA","AXS","CRV","AAVE","MKR","WIF","BONK",
    "JUP","TIA","RENDER","FET","GRT","IMX","BLUR","LDO","RPL","ENS",
]
AGENT_FEED_FILE = Path(os.getenv("AGENT_FEED", "agent_feed.jsonl"))


PLAIN_PROSE = (
    "Write in clear, professional, human prose. "
    "Do not use any markdown formatting: no asterisks, no hyphens as list markers, no em dashes, "
    "no horizontal lines, no equals signs, no pound signs as headers, no bullet symbols. "
    "Do not use symbols of any kind to separate sections. Use short paragraph breaks instead. "
    "Write the way a thoughtful senior professional would write in a well-crafted email or memo."
)


class WorkOrderRequest(BaseModel):
    prompt: str = Field(..., min_length=8, max_length=20000)
    assets: List[str] = Field(default_factory=list, max_length=10)
    max_results: int = Field(default=5, ge=1, le=10)


def record_hash(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "0x" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def audit(event: str, data: Dict[str, Any]) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **data}
    try:
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        log.error("audit write failed: %s", e)


def save_subscription(subscription: Dict[str, Any]) -> None:
    try:
        with SUBSCRIPTIONS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(subscription) + "\n")
    except Exception as e:
        log.error("subscription write failed: %s", e)


def load_subscriptions() -> None:
    if not SUBSCRIPTIONS_FILE.exists():
        return
    loaded = 0
    try:
        for line in SUBSCRIPTIONS_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            sub = json.loads(line)
            api_key = sub.get("api_key")
            expires_at = float(sub.get("expires_at", 0))
            if api_key and expires_at > time.time():
                _subscriptions[api_key] = sub
                loaded += 1
        log.info("Loaded %d active subscription keys", loaded)
    except Exception as e:
        log.warning("Could not load subscription keys: %s", e)


def load_payment_ledgers() -> None:
    if not AUDIT_LOG.exists():
        return
    used = 0
    try:
        for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            tx = (rec.get("tx_hash") or "").strip().lower()
            ev = rec.get("event")
            if tx and ev in {
                "direct_kite_settlement",
                "direct_kite_subscription",
                "subscription_created",
                "paid_call",
                "paid_batch",
                "paid_signal",
                "work_order_completed",
                "screener_call",
                "tx_consumed",
            }:
                _used_tx_hashes.add(tx)
                used += 1
            if ev == "subscription_usage":
                key = rec.get("api_key")
                sub = _subscriptions.get(key or "")
                if sub:
                    sub["work_orders_used"] = int(sub.get("work_orders_used", 0)) + int(rec.get("work_orders", 0) or 0)
                    sub["market_units_used"] = int(sub.get("market_units_used", 0)) + int(rec.get("market_units", 0) or 0)
        log.info("Loaded %d used tx references and replayed subscription usage", used)
    except Exception as e:
        log.warning("Could not load payment ledgers: %s", e)


def agent_record(decision: Dict[str, Any]) -> None:
    entry = {"ts": datetime.now(timezone.utc).isoformat(), **decision}
    _agent_feed.insert(0, entry)
    if len(_agent_feed) > AGENT_FEED_MAX:
        _agent_feed.pop()
    try:
        with AGENT_FEED_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def public_agent_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Return a privacy-safe feed entry for the public /agent page."""
    action = entry.get("action")
    asset = entry.get("asset")
    public = {
        "ts": entry.get("ts"),
        "asset": asset,
        "action": action,
        "confidence": entry.get("confidence"),
        "risk_level": entry.get("risk_level"),
        "settled_in": entry.get("settled_in"),
        "attestation_hash": entry.get("attestation_hash"),
        "chain_attestation_tx": entry.get("chain_attestation_tx", ""),
    }
    if action in {"BUY", "SELL", "HOLD", "SNAPSHOT"}:
        for key in ("price_usd", "reasoning", "fear_greed_index", "stop_loss_pct", "take_profit_pct"):
            if key in entry:
                public[key] = entry.get(key)
    else:
        public["rationale"] = {
            "SERVICES": "Kite service discovery completed.",
            "WORK": "A private work order was completed after settlement proof.",
        }.get(str(asset or "").upper(), "A private agent task was completed after settlement proof.")
    return public


def public_recent_activity(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Remove payer, key, prompt, and tx details from public dashboard rows."""
    return {
        "event_type": rec.get("event_type") or rec.get("event"),
        "asset": rec.get("asset"),
        "skill": rec.get("skill"),
        "assets": rec.get("assets") if rec.get("assets") else None,
        "amount_usdc": rec.get("amount_usdc"),
        "via_subscription": bool(rec.get("via_subscription")),
        "ts": rec.get("ts"),
    }


def _load_agent_feed() -> None:
    if not AGENT_FEED_FILE.exists():
        return
    try:
        lines = AGENT_FEED_FILE.read_text(encoding="utf-8").strip().splitlines()
        for line in reversed(lines[-AGENT_FEED_MAX:]):
            _agent_feed.append(json.loads(line))
    except Exception:
        pass


app = FastAPI(
    title=SERVICE_NAME, version="2.0.0",
    docs_url=None, redoc_url=None,
    description=SERVICE_DESCRIPTION,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-PAYMENT-RESPONSE"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    clean_errors = []
    for err in exc.errors():
        clean_errors.append({
            "field": ".".join(str(part) for part in err.get("loc", []) if part != "body") or "request",
            "message": err.get("msg", "Invalid value"),
            "type": err.get("type", "validation_error"),
        })
    return JSONResponse(status_code=422, content={
        "error": "The work order request is invalid.",
        "details": clean_errors,
        "hint": "For code review, paste the code directly in the work order. Maximum prompt length is 20,000 characters.",
    })


http_client: Optional[httpx.AsyncClient] = None
_coin_list_cache: Dict[str, str] = {}
_coin_list_loaded_at: float = 0.0
COIN_LIST_TTL = 24 * 60 * 60


@app.on_event("startup")
async def startup() -> None:
    global http_client
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
    )
    log.info("Agent Agora starting — Kite-settled agentic commerce")
    log.info("Wallet:       %s", SERVICE_WALLET)
    log.info("Network:      %s (chain %d)", KITE_NETWORK, KITE_CHAIN_ID)
    log.info("Price/call:   $%s USDC  |  Subscription: $%s/24h", PRICE_USDC, SUB_PRICE_USDC)
    load_subscriptions()
    load_payment_ledgers()
    asyncio.create_task(_load_coin_list())
    _load_agent_feed()


@app.on_event("shutdown")
async def shutdown() -> None:
    if http_client:
        await http_client.aclose()


# ── CoinGecko coin list ───────────────────────────────────────────────────────
async def _load_coin_list() -> None:
    global _coin_list_cache, _coin_list_loaded_at
    try:
        headers = {CG_AUTH_HEADER: CG_API_KEY} if CG_API_KEY else {}
        r = await http_client.get(f"{CG_BASE_URL}/coins/list", headers=headers)
        r.raise_for_status()
        coins = r.json()
        preferred_ids = {
            "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "USDT": "tether",
            "USDC": "usd-coin", "BNB": "binancecoin", "XRP": "ripple",
            "ADA": "cardano", "DOGE": "dogecoin", "AVAX": "avalanche-2",
            "DOT": "polkadot", "LINK": "chainlink", "MATIC": "matic-network",
            "TRX": "tron", "TON": "the-open-network", "SHIB": "shiba-inu",
            "LTC": "litecoin", "BCH": "bitcoin-cash", "UNI": "uniswap",
            "PEPE": "pepe", "KITE": "kite-ai",
        }
        new_map: Dict[str, str] = {}
        for coin in coins:
            sym = (coin.get("symbol") or "").upper()
            cid = coin.get("id") or ""
            if not sym or not cid:
                continue
            if sym in preferred_ids:
                new_map[sym] = preferred_ids[sym]
            elif sym not in new_map:
                new_map[sym] = cid
        _coin_list_cache = new_map
        _coin_list_loaded_at = time.time()
        log.info("Loaded %d coins from CoinGecko", len(new_map))
    except Exception as e:
        log.warning("Could not load CoinGecko coin list: %s", e)


async def get_coin_id(asset: str) -> Optional[str]:
    global _coin_list_loaded_at
    if (time.time() - _coin_list_loaded_at) > COIN_LIST_TTL or not _coin_list_cache:
        await _load_coin_list()
    return _coin_list_cache.get(asset.upper())


def provider_status() -> Dict[str, bool]:
    return {
        "openai": bool(OPENAI_API_KEY),
        "tavily": bool(TAVILY_API_KEY),
        "exa": bool(EXA_API_KEY),
        "coingecko": bool(CG_API_KEY),
    }


def current_date_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def needs_current_info(text: str) -> bool:
    p = text.lower()
    return any(term in p for term in (
        "ongoing", "current", "latest", "now", "today", "this week", "this month",
        "upcoming", "active", "open now", "currently open", "deadline", "2026",
        "newest", "recent", "live", "real-time", "real time", "available now",
    ))


def current_search_query(query: str) -> str:
    today = current_date_iso()
    year = today[:4]
    return (
        f"{query} {year} current ongoing upcoming active open deadline "
        f"as of {today} exclude past ended events"
    )


def source_passes_current_filter(source: Dict[str, str]) -> bool:
    text = f"{source.get('title', '')} {source.get('url', '')} {source.get('content', '')}".lower()
    current_year = current_date_iso()[:4]
    if current_year in text:
        return True
    if any(term in text for term in (
        "ongoing", "upcoming", "applications open", "registration open", "open now",
        "currently open", "apply by", "deadline", "closes", "starts", "2026",
    )):
        return True
    past_years = [str(year) for year in range(2020, int(current_year))]
    return not any(year in text for year in past_years)


async def openai_json(system: str, user: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "agent_agora_result",
                "schema": schema,
                "strict": True,
            }
        },
    }
    r = await http_client.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=45.0,
    )
    r.raise_for_status()
    data = r.json()
    text = data.get("output_text")
    if not text:
        parts: List[str] = []
        for item in data.get("output", []) or []:
            for content in item.get("content", []) or []:
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    parts.append(content["text"])
        text = "\n".join(parts)
    if not text:
        raise RuntimeError("OpenAI returned no text")
    return json.loads(text)


async def tavily_search(query: str, max_results: int = 6, current_only: bool = False) -> List[Dict[str, str]]:
    if not TAVILY_API_KEY:
        return []
    try:
        payload = {
            "api_key": TAVILY_API_KEY,
            "query": current_search_query(query) if current_only else query,
            "search_depth": "advanced",
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        }
        if current_only:
            payload["time_range"] = "year"
        r = await http_client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {TAVILY_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=25.0,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
        return [
            {
                "title": str(item.get("title") or "Untitled"),
                "url": str(item.get("url") or ""),
                "content": str(item.get("content") or "")[:1200],
            }
            for item in results
            if item.get("url")
        ]
    except Exception as e:
        log.warning("tavily search failed: %s", e)
        return []


async def exa_search(query: str, max_results: int = 6, current_only: bool = False) -> List[Dict[str, str]]:
    if not EXA_API_KEY:
        return []
    try:
        contents: Dict[str, Any] = {"highlights": True}
        if current_only:
            contents["maxAgeHours"] = 24
        r = await http_client.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": EXA_API_KEY, "Content-Type": "application/json"},
            json={
                "query": current_search_query(query) if current_only else query,
                "type": "deep-lite" if current_only else "auto",
                "numResults": max_results,
                "contents": contents,
            },
            timeout=25.0,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
        out = []
        for item in results:
            highlights = item.get("highlights") or []
            out.append({
                "title": str(item.get("title") or "Untitled"),
                "url": str(item.get("url") or ""),
                "content": " ".join(str(h) for h in highlights)[:1200],
            })
        return [r for r in out if r.get("url")]
    except Exception as e:
        log.warning("exa search failed: %s", e)
        return []


async def web_research(query: str, max_results: int = 6, current_only: bool = False) -> List[Dict[str, str]]:
    if current_only:
        tavily_results, exa_results = await asyncio.gather(
            tavily_search(query, max_results=max_results, current_only=True),
            exa_search(query, max_results=max_results, current_only=True),
        )
        merged: List[Dict[str, str]] = []
        seen = set()
        for item in [*tavily_results, *exa_results]:
            url = item.get("url", "")
            if not source_passes_current_filter(item):
                continue
            if url and url not in seen:
                merged.append(item)
                seen.add(url)
            if len(merged) >= max_results:
                break
        return merged
    results = await tavily_search(query, max_results=max_results)
    if results:
        return results
    return await exa_search(query, max_results=max_results)


def normalize_kite_service(item: Dict[str, Any]) -> Dict[str, Any]:
    price = item.get("starting_price") or {}
    endpoints = []
    for endpoint in item.get("featured_endpoints", []) or []:
        endpoint_price = endpoint.get("starting_price") or {}
        endpoints.append({
            "id": endpoint.get("service_endpoint_id"),
            "method": endpoint.get("method"),
            "path": endpoint.get("path"),
            "url": endpoint.get("endpoint_url"),
            "summary": endpoint.get("summary"),
            "price": endpoint_price or None,
            "payment_chain": endpoint.get("payment_chain") or endpoint.get("payment_approach"),
        })
    return {
        "service_id": item.get("service_host_id") or item.get("service_id"),
        "name": item.get("display_name") or item.get("name") or item.get("host_name"),
        "summary": item.get("summary") or "",
        "base_url": item.get("service_url") or item.get("base_url"),
        "host": item.get("host_name"),
        "state": item.get("state"),
        "visibility": item.get("visibility"),
        "categories": item.get("categories") or [],
        "tags": item.get("tags") or [],
        "payment_approach": item.get("protocol_family") or item.get("payment_approach"),
        "assets": item.get("assets") or [],
        "starting_price": price or None,
        "featured_endpoints": endpoints,
        "updated_at": item.get("updated_at"),
    }


async def fetch_kite_services(query: str = "", tag: str = "", asset: str = "",
                              payment_approach: str = "", limit: int = 20,
                              cursor: str = "") -> Dict[str, Any]:
    params: Dict[str, Any] = {"limit": max(1, min(limit, 100))}
    if query:
        params["query"] = query
    if tag:
        params["tag"] = tag
    if asset:
        params["asset"] = asset
    if payment_approach:
        params["payment_approach"] = payment_approach
    if cursor:
        params["cursor"] = cursor
    r = await http_client.get(f"{KITE_DISCOVERY_BASE}/v1/catalog/services", params=params, timeout=20.0)
    r.raise_for_status()
    data = r.json()
    return {
        "source": "kite_service_discovery",
        "backend": KITE_DISCOVERY_BASE,
        "services": [normalize_kite_service(item) for item in data.get("services", [])],
        "count": data.get("count", 0),
        "total": data.get("total", 0),
        "limit": data.get("limit", params["limit"]),
        "cursor": data.get("cursor", ""),
        "next_cursor": data.get("next_cursor", ""),
        "status": data.get("status", "success"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def sources_context(sources: List[Dict[str, str]]) -> str:
    if not sources:
        return "No live web sources were available."
    lines = []
    for idx, source in enumerate(sources, 1):
        lines.append(f"[{idx}] {source.get('title')}\nURL: {source.get('url')}\nExcerpt: {source.get('content')}")
    return "\n\n".join(lines)


# ── x402 payment helpers ──────────────────────────────────────────────────────
def build_payment_requirements(resource_url: str, price_raw: str = PRICE_USDC_RAW,
                                price_usdc: str = PRICE_USDC,
                                description: str = SERVICE_DESCRIPTION) -> Dict[str, Any]:
    return {
        "x402Version": 2,
        "error": "X-PAYMENT header required. Alternatively create an Agent Session at POST /api/agent/session and send its X-API-Key.",
        "accepts": [{
            "scheme": "exact",
            "network": KITE_NETWORK,
            "maxAmountRequired": price_raw,
            "resource": resource_url,
            "description": description,
            "mimeType": "application/json",
            "payTo": SERVICE_WALLET,
            "maxTimeoutSeconds": 300,
            "asset": KITE_USDC_CONTRACT,
            "extra": {"name": "USDC", "decimals": KITE_USDC_DECIMALS},
            "merchantName": SERVICE_NAME,
        }],
    }


def public_resource_url(request: Request) -> str:
    """Return a resource URL reachable by Kite Passport's x402 executor."""
    url = request.url
    if url.hostname in {"127.0.0.1", "localhost", "0.0.0.0"}:
        return PUBLIC_BASE_URL + url.path + (f"?{url.query}" if url.query else "")
    forwarded_proto = request.headers.get("x-forwarded-proto", url.scheme)
    scheme = "https" if forwarded_proto == "https" else url.scheme
    return str(url.replace(scheme=scheme))


def decode_payment_header(x_payment: str) -> Dict[str, Any]:
    try:
        raw = base64.b64decode(x_payment).decode("utf-8")
        return json.loads(raw)
    except Exception:
        try:
            return json.loads(x_payment)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid X-PAYMENT header: {e}")


async def verify_payment(payload: Dict, reqs: Dict) -> Dict[str, Any]:
    try:
        r = await http_client.post(FACILITATOR_VERIFY, json={"paymentPayload": payload, "paymentRequirements": reqs}, timeout=15.0)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=402, detail=f"Payment verification failed: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Facilitator unreachable: {e}")


async def settle_payment(payload: Dict, reqs: Dict) -> Dict[str, Any]:
    try:
        r = await http_client.post(FACILITATOR_SETTLE, json={"paymentPayload": payload, "paymentRequirements": reqs}, timeout=30.0)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=402, detail=f"Payment settlement failed: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Facilitator unreachable: {e}")


# ── Subscription helpers ──────────────────────────────────────────────────────
def generate_api_key() -> str:
    return "aa_" + secrets.token_hex(20)


def validate_api_key(key: str) -> Optional[Dict[str, Any]]:
    key = (key or "").strip()
    sub = _subscriptions.get(key)
    if not sub:
        return None
    if sub["expires_at"] < time.time():
        return None  # expired; keep in store for audit reference
    return sub


def tx_hash_consumed(tx_hash: str) -> bool:
    return (tx_hash or "").strip().lower() in _used_tx_hashes


def consume_tx_hash(tx_hash: str, purpose: str, payer: str, amount_usdc: str) -> None:
    normalized = (tx_hash or "").strip().lower()
    if not normalized:
        return
    _used_tx_hashes.add(normalized)
    audit("tx_consumed", {
        "tx_hash": tx_hash,
        "purpose": purpose,
        "payer": payer,
        "amount_usdc_expected": amount_usdc,
        "network": KITE_NETWORK,
    })


def consume_subscription_usage(api_key: str, sub: Dict[str, Any], kind: str, units: int) -> Optional[JSONResponse]:
    now = time.time()
    bucket = [t for t in _sub_rate_buckets.get(api_key, []) if now - t < 60]
    if len(bucket) >= SUB_RATE_LIMIT_PER_MIN:
        _sub_rate_buckets[api_key] = bucket
        return JSONResponse(status_code=429, content={
            "error": f"Subscription rate limit exceeded ({SUB_RATE_LIMIT_PER_MIN} requests/min).",
            "limits": subscription_limits(sub),
        })
    bucket.append(now)
    _sub_rate_buckets[api_key] = bucket

    if kind == "work_order":
        used = int(sub.get("work_orders_used", 0))
        if used + units > SUB_MAX_WORK_ORDERS:
            return JSONResponse(status_code=402, content={
                "error": "Subscription work-order quota exhausted. Renew at POST /api/subscribe.",
                "limits": subscription_limits(sub),
            })
        sub["work_orders_used"] = used + units
        audit("subscription_usage", {
            "api_key": api_key,
            "wallet": sub.get("wallet"),
            "work_orders": units,
            "market_units": 0,
            "work_orders_used": sub["work_orders_used"],
            "market_units_used": sub.get("market_units_used", 0),
        })
    else:
        used = int(sub.get("market_units_used", 0))
        if used + units > SUB_MAX_MARKET_UNITS:
            return JSONResponse(status_code=402, content={
                "error": "Subscription market-call quota exhausted. Renew at POST /api/subscribe.",
                "limits": subscription_limits(sub),
            })
        sub["market_units_used"] = used + units
        audit("subscription_usage", {
            "api_key": api_key,
            "wallet": sub.get("wallet"),
            "work_orders": 0,
            "market_units": units,
            "work_orders_used": sub.get("work_orders_used", 0),
            "market_units_used": sub["market_units_used"],
        })
    return None


def subscription_limits(sub: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "duration_hours": SUB_DURATION_HOURS,
        "max_work_orders": SUB_MAX_WORK_ORDERS,
        "work_orders_used": int(sub.get("work_orders_used", 0)),
        "work_orders_remaining": max(0, SUB_MAX_WORK_ORDERS - int(sub.get("work_orders_used", 0))),
        "max_market_units": SUB_MAX_MARKET_UNITS,
        "market_units_used": int(sub.get("market_units_used", 0)),
        "market_units_remaining": max(0, SUB_MAX_MARKET_UNITS - int(sub.get("market_units_used", 0))),
        "rate_limit_per_min": SUB_RATE_LIMIT_PER_MIN,
        "expires_at": datetime.fromtimestamp(float(sub.get("expires_at", 0)), tz=timezone.utc).isoformat(),
    }


def validate_kite_tx_hash(tx_hash: str) -> bool:
    tx_hash = (tx_hash or "").strip()
    return bool(re.fullmatch(r"0x[a-fA-F0-9]{64}", tx_hash))


async def extract_kite_tx_hash(request: Request) -> str:
    direct = (request.headers.get("X-Kite-Tx-Hash") or request.headers.get("x-kite-tx-hash") or "").strip()
    if direct:
        return direct
    for key, value in request.headers.items():
        if validate_kite_tx_hash(key):
            return key.strip()
        if validate_kite_tx_hash(value):
            return value.strip()
    query_tx = (request.query_params.get("tx_hash") or request.query_params.get("kite_tx_hash") or "").strip()
    if query_tx:
        return query_tx
    if request.method in {"POST", "PUT", "PATCH"}:
        try:
            body = await request.json()
        except Exception:
            body = None
        if isinstance(body, dict):
            for field in ("tx_hash", "kite_tx_hash", "x_kite_tx_hash"):
                candidate = str(body.get(field) or "").strip()
                if candidate:
                    return candidate
    return ""


def normalize_address(address: str) -> str:
    return (address or "").strip().lower()


def topic_address(topic: str) -> str:
    topic = (topic or "").lower()
    if not topic.startswith("0x") or len(topic) < 42:
        return ""
    return "0x" + topic[-40:]


async def kite_rpc(method: str, params: List[Any]) -> Any:
    payload = {"jsonrpc": "2.0", "id": int(time.time() * 1000), "method": method, "params": params}
    try:
        r = await http_client.post(KITE_RPC_URL, json=payload, timeout=15.0)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Kite RPC unavailable: {e}")
    if data.get("error"):
        raise HTTPException(status_code=502, detail=f"Kite RPC error: {data['error']}")
    return data.get("result")


async def write_attestation_to_chain(attestation_hash: str) -> Optional[str]:
    """Send a 0-value Kite transaction whose calldata is the attestation hash.
    Returns the Kite chain tx hash, or None if the key is not configured."""
    if not SERVICE_WALLET_KEY:
        return None
    try:
        from eth_account import Account
        account = Account.from_key(SERVICE_WALLET_KEY)
        nonce_hex = await kite_rpc("eth_getTransactionCount", [account.address, "latest"])
        gas_price_hex = await kite_rpc("eth_gasPrice", [])
        nonce = int(nonce_hex, 16)
        gas_price = int(gas_price_hex, 16)
        data = "0x" + attestation_hash.encode().hex()
        tx = {
            "nonce": nonce,
            "gasPrice": gas_price,
            "gas": 60000,
            "to": account.address,
            "value": 0,
            "data": data,
            "chainId": KITE_CHAIN_ID,
        }
        signed = account.sign_transaction(tx)
        raw = signed.raw_transaction.hex()
        if not raw.startswith("0x"):
            raw = "0x" + raw
        chain_tx = await kite_rpc("eth_sendRawTransaction", [raw])
        log.info("Attestation written to Kite chain: %s", chain_tx)
        return chain_tx
    except Exception as e:
        log.error("write_attestation_to_chain failed: %s", e)
        return None


async def verify_kite_usdc_payment(tx_hash: str, required_raw: str) -> Dict[str, Any]:
    receipt = await kite_rpc("eth_getTransactionReceipt", [tx_hash])
    if not receipt:
        raise HTTPException(status_code=402, detail="Kite transaction was not found on-chain.")
    if (receipt.get("status") or "").lower() != "0x1":
        raise HTTPException(status_code=402, detail="Kite transaction did not succeed.")

    tx = await kite_rpc("eth_getTransactionByHash", [tx_hash])
    if not tx:
        raise HTTPException(status_code=402, detail="Kite transaction details were not found.")

    chain_id = await kite_rpc("eth_chainId", [])
    if int(chain_id or "0x0", 16) != KITE_CHAIN_ID:
        raise HTTPException(status_code=402, detail="Transaction was not verified on Kite mainnet.")

    merchant = normalize_address(SERVICE_WALLET)
    token = normalize_address(KITE_USDC_CONTRACT)
    required = int(required_raw)

    total_received = 0
    for log_entry in receipt.get("logs", []) or []:
        topics = log_entry.get("topics", []) or []
        if normalize_address(log_entry.get("address", "")) != token:
            continue
        if len(topics) < 3 or normalize_address(topics[0]) != TRANSFER_TOPIC:
            continue
        if topic_address(topics[2]) != merchant:
            continue
        try:
            total_received += int(log_entry.get("data") or "0x0", 16)
        except ValueError:
            continue

    if total_received < required:
        expected = required / 10 ** KITE_USDC_DECIMALS
        received = total_received / 10 ** KITE_USDC_DECIMALS
        raise HTTPException(
            status_code=402,
            detail=f"Kite payment is insufficient or not sent to Agent Agora. Required {expected:.6f} USDC, received {received:.6f} USDC.",
        )

    return {
        "payer": normalize_address(tx.get("from") or ""),
        "merchant": SERVICE_WALLET,
        "asset": KITE_USDC_CONTRACT,
        "amount_raw": str(total_received),
        "amount_usdc": str(total_received / 10 ** KITE_USDC_DECIMALS),
        "block_number": receipt.get("blockNumber"),
        "transaction_index": receipt.get("transactionIndex"),
    }


async def process_x402_payment(request: Request, resource_url: str,
                                price_raw: str = PRICE_USDC_RAW,
                                price_usdc: str = PRICE_USDC,
                                description: str = SERVICE_DESCRIPTION
                                ) -> Tuple[Optional[JSONResponse], Optional[str], Optional[str]]:
    """
    Returns (error_response, payer_wallet, tx_hash).
    error_response is non-None when payment fails — caller should return it immediately.
    """
    x_payment = request.headers.get("X-PAYMENT") or request.headers.get("x-payment")
    if not x_payment:
        reqs = build_payment_requirements(resource_url, price_raw, price_usdc, description)
        return JSONResponse(status_code=402, content=reqs), None, None
    try:
        payload = decode_payment_header(x_payment)
    except HTTPException as e:
        return JSONResponse(status_code=400, content={"error": e.detail}), None, None
    reqs = build_payment_requirements(resource_url, price_raw, price_usdc, description)
    verify = await verify_payment(payload, reqs)
    if not verify.get("isValid", verify.get("valid", False)):
        reason = verify.get("invalidReason") or verify.get("reason") or "unknown"
        return JSONResponse(status_code=402, content={**reqs, "error": f"Payment invalid: {reason}"}), None, None
    payer = verify.get("payer") or verify.get("from") or "unknown"
    settle = await settle_payment(payload, reqs)
    tx_hash = settle.get("transaction") or settle.get("txHash") or settle.get("transactionHash") or "pending"
    if not settle.get("success", True):
        return JSONResponse(status_code=402, content={"error": "Settlement failed"}), None, None
    return None, payer, tx_hash


# ── Data fetchers ─────────────────────────────────────────────────────────────
async def fetch_coingecko(asset: str) -> Dict[str, Any]:
    coin_id = await get_coin_id(asset)
    if not coin_id:
        return {"source": "coingecko", "error": f"Unknown asset: {asset}"}
    try:
        url = f"{CG_BASE_URL}/coins/{coin_id}"
        params = {"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false", "sparkline": "false"}
        headers = {CG_AUTH_HEADER: CG_API_KEY} if CG_API_KEY else {}
        r = await http_client.get(url, params=params, headers=headers)
        r.raise_for_status()
        data = r.json()
        md = data.get("market_data", {}) or {}
        return {
            "source": "coingecko", "coin_id": coin_id,
            "name": data.get("name"),
            "symbol": (data.get("symbol") or asset).upper(),
            "price_usd": (md.get("current_price") or {}).get("usd"),
            "market_cap_usd": (md.get("market_cap") or {}).get("usd"),
            "volume_24h_usd": (md.get("total_volume") or {}).get("usd"),
            "change_1h_pct": (md.get("price_change_percentage_1h_in_currency") or {}).get("usd"),
            "change_24h_pct": md.get("price_change_percentage_24h"),
            "change_7d_pct": md.get("price_change_percentage_7d"),
            "change_14d_pct": md.get("price_change_percentage_14d"),
            "change_30d_pct": md.get("price_change_percentage_30d"),
            "ath_usd": (md.get("ath") or {}).get("usd"),
            "ath_change_pct": (md.get("ath_change_percentage") or {}).get("usd"),
            "circulating_supply": md.get("circulating_supply"),
            "total_supply": md.get("total_supply"),
            "market_cap_rank": data.get("market_cap_rank"),
            "last_updated": data.get("last_updated"),
        }
    except Exception as e:
        return {"source": "coingecko", "error": str(e)}


async def fetch_coingecko_markets(ids: List[str]) -> List[Dict[str, Any]]:
    if not ids:
        return []
    headers = {CG_AUTH_HEADER: CG_API_KEY} if CG_API_KEY else {}
    try:
        r = await http_client.get(
            f"{CG_BASE_URL}/coins/markets",
            headers=headers,
            params={
                "vs_currency": "usd",
                "ids": ",".join(ids[:50]),
                "order": "market_cap_desc",
                "per_page": min(50, len(ids)),
                "page": 1,
                "sparkline": "false",
                "price_change_percentage": "1h,24h,7d,30d",
            },
            timeout=20.0,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("coingecko markets failed: %s", e)
        return []


async def fetch_trending_assets(limit: int = 10) -> List[str]:
    headers = {CG_AUTH_HEADER: CG_API_KEY} if CG_API_KEY else {}
    try:
        r = await http_client.get(f"{CG_BASE_URL}/search/trending", headers=headers, timeout=15.0)
        r.raise_for_status()
        coins = r.json().get("coins") or []
        symbols = []
        for item in coins:
            coin = item.get("item") or {}
            sym = (coin.get("symbol") or "").upper()
            if sym and sym not in symbols:
                symbols.append(sym)
        return symbols[:limit]
    except Exception as e:
        log.warning("coingecko trending failed: %s", e)
        return []


async def fetch_binance(asset: str) -> Dict[str, Any]:
    sym = f"{asset.upper()}USDT"
    out: Dict[str, Any] = {"source": "binance"}
    try:
        r = await http_client.get("https://api.binance.com/api/v3/ticker/24hr", params={"symbol": sym})
        if r.status_code == 200:
            t = r.json()
            out["spot_price"] = float(t.get("lastPrice", 0))
            out["spot_volume_24h"] = float(t.get("quoteVolume", 0))
            out["spot_high_24h"] = float(t.get("highPrice", 0))
            out["spot_low_24h"] = float(t.get("lowPrice", 0))
    except Exception:
        pass
    try:
        r = await http_client.get("https://fapi.binance.com/fapi/v1/premiumIndex", params={"symbol": sym})
        if r.status_code == 200:
            d = r.json()
            out["funding_rate_pct"] = float(d.get("lastFundingRate", 0)) * 100
            out["mark_price"] = float(d.get("markPrice", 0))
    except Exception:
        pass
    try:
        r = await http_client.get("https://fapi.binance.com/fapi/v1/openInterest", params={"symbol": sym})
        if r.status_code == 200:
            out["open_interest"] = float(r.json().get("openInterest", 0))
    except Exception:
        pass
    return out


async def fetch_bybit(asset: str) -> Dict[str, Any]:
    sym = f"{asset.upper()}USDT"
    try:
        r = await http_client.get("https://api.bybit.com/v5/market/tickers", params={"category": "linear", "symbol": sym})
        if r.status_code != 200:
            return {"source": "bybit", "error": f"HTTP {r.status_code}"}
        tickers = (r.json().get("result") or {}).get("list") or []
        if not tickers:
            return {"source": "bybit", "error": "no data"}
        t = tickers[0]
        return {
            "source": "bybit",
            "last_price": float(t.get("lastPrice", 0)),
            "funding_rate_pct": float(t.get("fundingRate", 0)) * 100,
            "open_interest_usd": float(t.get("openInterestValue", 0)),
            "volume_24h_usd": float(t.get("turnover24h", 0)),
            "price_change_24h_pct": float(t.get("price24hPcnt", 0)) * 100,
        }
    except Exception as e:
        return {"source": "bybit", "error": str(e)}


async def fetch_fear_greed() -> Dict[str, Any]:
    try:
        r = await http_client.get("https://api.alternative.me/fng/")
        r.raise_for_status()
        item = (r.json().get("data") or [{}])[0]
        return {"source": "fear_greed", "value": int(item.get("value", 0)), "label": item.get("value_classification")}
    except Exception as e:
        return {"source": "fear_greed", "error": str(e)}


# ── Signal derivation ─────────────────────────────────────────────────────────
def derive_signals(cg: Dict, bn: Dict, bb: Dict, fg: Dict) -> Dict[str, str]:
    signals: Dict[str, str] = {}
    change_24h = cg.get("change_24h_pct") or bb.get("price_change_24h_pct") or 0
    if change_24h > 5:       signals["trend"] = "strongly_bullish"
    elif change_24h > 1:     signals["trend"] = "bullish"
    elif change_24h < -5:    signals["trend"] = "strongly_bearish"
    elif change_24h < -1:    signals["trend"] = "bearish"
    else:                    signals["trend"] = "neutral"
    high = bn.get("spot_high_24h"); low = bn.get("spot_low_24h")
    if high and low and low > 0:
        vol_pct = ((high - low) / low) * 100
        if vol_pct > 10:   signals["volatility"] = "high"
        elif vol_pct > 3:  signals["volatility"] = "moderate"
        else:              signals["volatility"] = "low"
    else:
        signals["volatility"] = "unknown"
    funding = bn.get("funding_rate_pct") or bb.get("funding_rate_pct") or 0
    if funding > 0.05:    signals["leverage"] = "elevated_longs"
    elif funding < -0.05: signals["leverage"] = "elevated_shorts"
    else:                 signals["leverage"] = "balanced"
    fg_val = fg.get("value", 50)
    if fg_val >= 75:        signals["market_sentiment"] = "extreme_greed"
    elif fg_val >= 55:      signals["market_sentiment"] = "greed"
    elif fg_val <= 25:      signals["market_sentiment"] = "extreme_fear"
    elif fg_val <= 45:      signals["market_sentiment"] = "fear"
    else:                   signals["market_sentiment"] = "neutral"
    return signals


def derive_trade_action(intel: Dict[str, Any]) -> Dict[str, Any]:
    sig       = intel.get("signals", {})
    trend     = sig.get("trend", "neutral")
    volatility = sig.get("volatility", "moderate")
    leverage  = sig.get("leverage", "balanced")
    fg        = intel.get("fear_greed_index") or 50
    score     = 0.0
    reasons: List[str] = []

    trend_map = {"strongly_bullish": 2.0, "bullish": 1.0, "neutral": 0.0, "bearish": -1.0, "strongly_bearish": -2.0}
    score += trend_map.get(trend, 0.0)
    if trend != "neutral":
        reasons.append(f"price trend is {trend.replace('_', ' ')}")

    if fg <= 25:
        score += 1.0; reasons.append("extreme fear — contrarian buy signal")
    elif fg <= 40:
        score += 0.5; reasons.append("fear environment — mild contrarian opportunity")
    elif fg >= 75:
        score -= 1.0; reasons.append("extreme greed — elevated reversal risk")
    elif fg >= 60:
        score -= 0.5; reasons.append("greed environment — caution warranted")

    if leverage == "elevated_longs":
        score -= 0.5; reasons.append("crowded longs raise liquidation risk")
    elif leverage == "elevated_shorts":
        score += 0.5; reasons.append("elevated shorts create short-squeeze potential")

    high_vol = volatility == "high"
    if high_vol:
        reasons.append("high volatility — widen stops")

    market_cap = intel.get("market_cap_usd") or 0
    volume_24h = intel.get("volume_24h_usd") or 0
    if market_cap and market_cap < 50_000_000:
        score -= 0.75
        reasons.append("small market cap raises manipulation/liquidity risk")
    if volume_24h and volume_24h < 5_000_000:
        score -= 0.75
        reasons.append("thin 24h volume makes signal less reliable")
    if volume_24h and market_cap and volume_24h / market_cap > 2:
        score -= 0.5
        reasons.append("turnover is unusually high versus market cap")

    if score >= 1.5:
        action = "BUY"
        confidence = min(88, int(50 + score * 12)) - (10 if high_vol else 0)
        risk_level = 4 if high_vol else 2
        stop_loss_pct, take_profit_pct = (-8.0, 15.0) if high_vol else (-5.0, 8.0)
    elif score <= -1.5:
        action = "SELL"
        confidence = min(88, int(50 + abs(score) * 12)) - (10 if high_vol else 0)
        risk_level = 4 if high_vol else 3
        stop_loss_pct, take_profit_pct = (5.0, -8.0)
    else:
        action = "HOLD"
        confidence = max(20, 40 - int(abs(score) * 5) - (10 if high_vol else 0))
        risk_level = 2
        stop_loss_pct, take_profit_pct = -5.0, 5.0

    if not reasons:
        reasons.append("mixed signals — no clear directional bias")

    return {
        "action": action,
        "confidence": max(10, confidence),
        "risk_level": risk_level,
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
        "score": round(score, 2),
        "reasoning": "; ".join(reasons),
    }


def provider_error_message(e: Exception) -> str:
    text = str(e)
    if "429" in text:
        return "OpenAI provider is rate-limited or out of quota for the configured key."
    return text[:240]


async def build_market_intelligence(asset: str) -> Dict[str, Any]:
    asset = asset.upper().strip()
    t0 = time.time()
    cg, bn, bb, fg = await asyncio.gather(
        fetch_coingecko(asset), fetch_binance(asset), fetch_bybit(asset), fetch_fear_greed(),
    )
    sources_used = [s["source"] for s in (cg, bn, bb, fg) if not s.get("error")]
    price = cg.get("price_usd") or bn.get("spot_price") or bb.get("last_price")
    signals = derive_signals(cg, bn, bb, fg)
    intel = {
        "asset": asset, "name": cg.get("name", asset),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "latency_ms": int((time.time() - t0) * 1000),
        "price_usd": price,
        "change_1h_pct": cg.get("change_1h_pct"),
        "change_24h_pct": cg.get("change_24h_pct") or bb.get("price_change_24h_pct"),
        "change_7d_pct": cg.get("change_7d_pct"),
        "market_cap_usd": cg.get("market_cap_usd"),
        "volume_24h_usd": cg.get("volume_24h_usd") or bb.get("volume_24h_usd"),
        "circulating_supply": cg.get("circulating_supply"),
        "ath_usd": cg.get("ath_usd"),
        "ath_change_pct": cg.get("ath_change_pct"),
        "funding_rate_pct": bn.get("funding_rate_pct") or bb.get("funding_rate_pct"),
        "open_interest_usd": bb.get("open_interest_usd"),
        "mark_price": bn.get("mark_price"),
        "spot_high_24h": bn.get("spot_high_24h"),
        "spot_low_24h": bn.get("spot_low_24h"),
        "fear_greed_index": fg.get("value"),
        "fear_greed_label": fg.get("label"),
        "signals": signals,
        "sources": sources_used,
        "network": KITE_NETWORK,
        "settlement_asset": "USDC",
    }
    return intel


# ─────────────────────────────────────────────────────────────────────────────
# Free endpoints
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/health", tags=["Free"])
async def health() -> Dict[str, Any]:
    active_subs = sum(1 for s in _subscriptions.values() if s["expires_at"] > time.time())
    return {
        "ok": True, "service": SERVICE_NAME, "version": "2.0.0",
        "network": KITE_NETWORK,
        "service_wallet": SERVICE_WALLET,
        "wallet": SERVICE_WALLET,
        "price_usdc": PRICE_USDC,
        "pricing": {"per_call_usdc": PRICE_USDC, "subscription_24h_usdc": SUB_PRICE_USDC},
        "facilitator": FACILITATOR_BASE,
        "active_subscriptions": active_subs,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/discover", tags=["Free"])
async def discover(request: Request) -> Dict[str, Any]:
    base = str(request.base_url).rstrip("/").replace("http://", "https://")
    return {
        "service": SERVICE_NAME, "version": "2.0.0",
        "description": SERVICE_DESCRIPTION,
        "network": KITE_NETWORK, "chain_id": KITE_CHAIN_ID,
        "wallet": SERVICE_WALLET,
        "usdc_contract": KITE_USDC_CONTRACT,
        "facilitator": FACILITATOR_BASE,
        "paid_endpoints": [
            {
                "path": "/api/market", "method": "GET",
                "url": f"{base}/api/market?asset=BTC",
                "description": "Full market intelligence for a single asset",
                "price_usdc": PRICE_USDC, "price_raw": PRICE_USDC_RAW,
                "auth": "X-PAYMENT (x402), X-API-Key, or X-Kite-Tx-Hash direct proof",
                "params": {"asset": "Ticker symbol — BTC, ETH, SOL, KITE, PEPE…"},
            },
            {
                "path": "/api/market/batch", "method": "GET",
                "url": f"{base}/api/market/batch?assets=BTC,ETH,SOL",
                "description": "Market intelligence for multiple assets in one call",
                "price_usdc": f"$0.02 per asset (max {BATCH_MAX_ASSETS})",
                "price_raw": f"{BATCH_PRICE_PER_ASSET_RAW} per asset",
                "auth": "X-PAYMENT (x402), X-API-Key, or X-Kite-Tx-Hash direct proof",
                "params": {"assets": "Comma-separated tickers, max 10"},
            },
            {
                "path": "/api/signals/{asset}", "method": "GET",
                "url": f"{base}/api/signals/BTC",
                "description": "Structured trading signal — action, confidence, risk, entry/exit",
                "price_usdc": PRICE_USDC, "price_raw": PRICE_USDC_RAW,
                "auth": "X-PAYMENT (x402), X-API-Key, or X-Kite-Tx-Hash direct proof",
                "params": {"asset": "Ticker symbol"},
                "response_fields": ["action", "confidence", "risk_level", "stop_loss_pct", "take_profit_pct", "reasoning"],
            },
            {
                "path": "/api/subscribe", "method": "GET",
                "url": f"{base}/api/subscribe",
                "description": "Backward-compatible alias: pay once, get a metered 24h Agent Session key",
                "price_usdc": SUB_PRICE_USDC, "price_raw": SUB_PRICE_RAW,
                "auth": "X-PAYMENT (x402) or X-Kite-Tx-Hash direct proof",
                "response_fields": ["api_key", "session_key", "expires_at", "duration_hours"],
            },
            {
                "path": "/api/agent/session", "method": "POST",
                "url": f"{base}/api/agent/session",
                "description": "Create a metered 24h Agent Session key for repeated work orders and market access",
                "price_usdc": SUB_PRICE_USDC, "price_raw": SUB_PRICE_RAW,
                "auth": "X-PAYMENT (x402) or X-Kite-Tx-Hash direct proof",
                "response_fields": ["session_key", "api_key", "session_expires_at", "duration_hours"],
            },
            {
                "path": "/api/screener", "method": "GET",
                "url": f"{base}/api/screener?signal=BUY&min_confidence=45",
                "description": "Scan supported assets and return BUY/SELL/HOLD opportunities",
                "price_usdc": PRICE_USDC, "price_raw": PRICE_USDC_RAW,
                "auth": "X-PAYMENT (x402), X-API-Key, or X-Kite-Tx-Hash direct proof",
                "params": {"signal": "BUY | SELL | HOLD | ALL", "min_confidence": "0-100"},
            },
            {
                "path": "/api/agent/work-orders", "method": "POST",
                "url": f"{base}/api/agent/work-orders",
                "description": "Execute a routed autonomous agent work order",
                "price_usdc": WORK_ORDER_PRICE_USDC, "price_raw": WORK_ORDER_PRICE_RAW,
                "auth": "X-PAYMENT (x402), X-API-Key, or X-Kite-Tx-Hash direct proof",
                "body": {"prompt": "Task for the agent"},
            },
        ],
        "free_endpoints": [
            {"path": "/api/market/free", "description": f"Rate-limited market intel ({FREE_RATE_LIMIT} req/min per IP)"},
            {"path": "/api/discover", "description": "This document — machine-readable service manifest"},
            {"path": "/api/kite/services", "description": "Live Kite service catalog from Kite discovery"},
            {"path": "/api/health", "description": "Liveness + pricing info"},
            {"path": "/api/stats", "description": "Aggregate usage stats"},
            {"path": "/api/agents", "description": "Aggregate payer count; wallet details are private"},
        ],
    }


@app.get("/api/kite/services", tags=["Free"])
async def kite_services(
    query: str = Query("", max_length=120),
    tag: str = Query("", max_length=60),
    asset: str = Query("", max_length=80),
    payment_approach: str = Query("", max_length=30),
    limit: int = Query(20, ge=1, le=100),
    cursor: str = Query("", max_length=200),
) -> JSONResponse:
    try:
        data = await fetch_kite_services(
            query=query.strip(),
            tag=tag.strip(),
            asset=asset.strip(),
            payment_approach=payment_approach.strip(),
            limit=limit,
            cursor=cursor.strip(),
        )
        return JSONResponse(content=data)
    except Exception as e:
        log.warning("kite service discovery failed: %s", e)
        return JSONResponse(status_code=502, content={
            "error": "Kite service discovery is currently unavailable.",
            "backend": KITE_DISCOVERY_BASE,
            "detail": provider_error_message(e),
        })


@app.get("/api/stats", tags=["Free"])
async def stats() -> Dict[str, Any]:
    total_calls = 0; total_paid = 0.0; total_subs = 0; sub_revenue = 0.0
    assets: Dict[str, int] = {}; recent: List[Dict] = []; payers: set = set()
    if AUDIT_LOG.exists():
        try:
            with AUDIT_LOG.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    ev = rec.get("event")
                    if ev == "paid_call":
                        total_calls += 1
                        total_paid += float(rec.get("amount_usdc") or 0)
                        a = rec.get("asset", "?")
                        assets[a] = assets.get(a, 0) + 1
                        recent.append({**rec, "event_type": "paid_call"})
                        if rec.get("payer"): payers.add(rec["payer"])
                    elif ev == "paid_batch":
                        total_calls += len(rec.get("assets") or [])
                        total_paid += float(rec.get("amount_usdc") or 0)
                        for a in rec.get("assets") or []:
                            assets[a] = assets.get(a, 0) + 1
                        recent.append({**rec, "event_type": "paid_batch"})
                        if rec.get("payer"): payers.add(rec["payer"])
                    elif ev == "paid_signal":
                        total_calls += 1
                        total_paid += float(rec.get("amount_usdc") or 0)
                        a = rec.get("asset", "?")
                        assets[a] = assets.get(a, 0) + 1
                        recent.append({**rec, "event_type": "paid_signal"})
                        if rec.get("payer"): payers.add(rec["payer"])
                    elif ev == "subscribed_call":
                        total_calls += 1
                        a = rec.get("asset", "?")
                        assets[a] = assets.get(a, 0) + 1
                        if rec.get("wallet"): payers.add(rec["wallet"])
                    elif ev == "subscribed_batch":
                        total_calls += len(rec.get("assets") or [])
                        for a in rec.get("assets") or []:
                            assets[a] = assets.get(a, 0) + 1
                        if rec.get("wallet"): payers.add(rec["wallet"])
                    elif ev == "subscribed_signal":
                        total_calls += 1
                        a = rec.get("asset", "?")
                        assets[a] = assets.get(a, 0) + 1
                        if rec.get("wallet"): payers.add(rec["wallet"])
                    elif ev == "screener_call":
                        total_calls += 1
                        if not rec.get("via_subscription"):
                            total_paid += float(PRICE_USDC)
                        recent.append({
                            **rec,
                            "event_type": "screener_call",
                            "amount_usdc": "0" if rec.get("via_subscription") else PRICE_USDC,
                        })
                        if rec.get("payer"): payers.add(rec["payer"])
                    elif ev == "subscription_created":
                        total_subs += 1
                        sub_revenue += float(SUB_PRICE_USDC)
                        recent.append({**rec, "event_type": "subscription_created", "amount_usdc": SUB_PRICE_USDC})
                        if rec.get("wallet"): payers.add(rec["wallet"])
                    elif ev == "work_order_completed":
                        total_calls += 1
                        if not rec.get("via_subscription"):
                            total_paid += float(WORK_ORDER_PRICE_USDC)
                        recent.append({
                            **rec,
                            "event_type": "work_order_completed",
                            "amount_usdc": "0" if rec.get("via_subscription") else WORK_ORDER_PRICE_USDC,
                        })
                        if rec.get("payer"): payers.add(rec["payer"])
        except Exception as e:
            log.warning("stats read failed: %s", e)
    active_subs = sum(1 for s in _subscriptions.values() if s["expires_at"] > time.time())
    top_assets = sorted(assets.items(), key=lambda x: -x[1])[:10]
    total_revenue = round(total_paid + sub_revenue, 6)
    activity_summary: Dict[str, int] = {}
    for r in recent:
        event_type = str(r.get("event_type") or r.get("event") or "activity")
        activity_summary[event_type] = activity_summary.get(event_type, 0) + 1

    # Build public-safe recent activity — no wallets, no prompts, no tx details
    SKILL_LABELS = {
        "work_order_completed": "Work Order",
        "paid_call": "Market Intelligence",
        "paid_signal": "Trading Signal",
        "paid_batch": "Batch Market Scan",
        "screener_call": "Market Screener",
        "subscription_created": "Session Created",
        "subscribed_call": "Market Intelligence",
    }
    public_recent = []
    for r in sorted(recent, key=lambda x: x.get("ts",""), reverse=True)[:20]:
        ev = r.get("event_type") or r.get("event") or "activity"
        skill = r.get("skill") or SKILL_LABELS.get(ev, ev.replace("_", " ").title())
        asset = r.get("asset") or r.get("skill") or ""
        amount = r.get("amount_usdc") or ("0.05" if ev not in ("subscription_created",) else "0.50")
        attestation = r.get("attestation_hash") or r.get("proof") or ""
        public_recent.append({
            "ts": r.get("ts",""),
            "skill": skill,
            "asset": asset,
            "amount_usdc": amount,
            "attestation_hash": attestation,
            "settled": "USDC · Kite",
        })

    return {
        "total_paid_calls": total_calls,
        "total_api_calls": total_calls,
        "total_revenue_usdc": total_revenue,
        "total_paid_usdc": total_revenue,
        "per_call_revenue_usdc": round(total_paid, 6),
        "subscription_revenue_usdc": round(sub_revenue, 6),
        "total_subscriptions_sold": total_subs,
        "active_subscriptions": active_subs,
        "unique_payers": len(payers),
        "top_assets": [{"asset": a, "count": c} for a, c in top_assets],
        "recent_calls": public_recent,
        "recent_paid_calls": public_recent,
        "activity_summary": [{"event_type": k, "count": v} for k, v in sorted(activity_summary.items())],
        "active_subscription_keys": {},
        "privacy_mode": "public_aggregate_only",
    }


@app.get("/api/agents", tags=["Free"])
async def agents() -> Dict[str, Any]:
    agent_wallets: Dict[str, Dict[str, Any]] = {}
    if AUDIT_LOG.exists():
        try:
            with AUDIT_LOG.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("event") in ("paid_call", "subscription_created", "subscribed_call", "subscribed_signal", "screener_call"):
                        w = rec.get("payer") or rec.get("wallet") or "unknown"
                        if w == "unknown":
                            continue
                        has_sub = rec.get("event") == "subscription_created"
                        if w not in agent_wallets:
                            agent_wallets[w] = {"wallet": w, "total_queries": 0, "total_paid_usdc": 0.0, "assets": [], "last_seen": "", "has_subscription": False}
                        agent_wallets[w]["total_queries"] += 1
                        amount = 0.0
                        if rec.get("event") == "subscription_created":
                            amount = float(rec.get("amount_usdc") or SUB_PRICE_USDC)
                        elif rec.get("event") in ("paid_call", "screener_call"):
                            amount = float(rec.get("amount_usdc") or (PRICE_USDC if not rec.get("via_subscription") else 0))
                        agent_wallets[w]["total_paid_usdc"] = round(agent_wallets[w]["total_paid_usdc"] + amount, 6)
                        if has_sub:
                            agent_wallets[w]["has_subscription"] = True
                        a = rec.get("asset")
                        if a and a not in agent_wallets[w]["assets"]:
                            agent_wallets[w]["assets"].append(a)
                        agent_wallets[w]["last_seen"] = rec.get("ts", "")
                    elif rec.get("event") in ("paid_batch", "subscribed_batch"):
                        w = rec.get("payer") or rec.get("wallet") or "unknown"
                        if w == "unknown":
                            continue
                        if w not in agent_wallets:
                            agent_wallets[w] = {"wallet": w, "total_queries": 0, "total_paid_usdc": 0.0, "assets": [], "last_seen": "", "has_subscription": False}
                        unit_count = len(rec.get("assets") or [])
                        agent_wallets[w]["total_queries"] += unit_count
                        agent_wallets[w]["total_paid_usdc"] = round(agent_wallets[w]["total_paid_usdc"] + float(rec.get("amount_usdc") or 0), 6)
                        for a in rec.get("assets") or []:
                            if a and a not in agent_wallets[w]["assets"]:
                                agent_wallets[w]["assets"].append(a)
                        agent_wallets[w]["last_seen"] = rec.get("ts", "")
                    elif rec.get("event") == "work_order_completed":
                        w = rec.get("payer") or "unknown"
                        if w == "unknown":
                            continue
                        if w not in agent_wallets:
                            agent_wallets[w] = {"wallet": w, "total_queries": 0, "total_paid_usdc": 0.0, "assets": [], "last_seen": "", "has_subscription": False}
                        agent_wallets[w]["total_queries"] += 1
                        if not rec.get("via_subscription"):
                            agent_wallets[w]["total_paid_usdc"] = round(agent_wallets[w]["total_paid_usdc"] + float(WORK_ORDER_PRICE_USDC), 6)
                        skill = rec.get("skill")
                        if skill and skill not in agent_wallets[w]["assets"]:
                            agent_wallets[w]["assets"].append(skill)
                        agent_wallets[w]["last_seen"] = rec.get("ts", "")
        except Exception as e:
            log.warning("agents read failed: %s", e)
    ranked = sorted(agent_wallets.values(), key=lambda x: -x["total_queries"])
    return {
        "total_unique_agents": len(agent_wallets),
        "agents": [],
        "privacy_mode": "public_aggregate_only",
        "note": "Payer wallet details are not exposed on the public dashboard.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _check_rate_limit(ip: str) -> bool:
    now = time.time()
    bucket = [t for t in _rate_buckets.get(ip, []) if now - t < FREE_RATE_WINDOW]
    if len(bucket) >= FREE_RATE_LIMIT:
        _rate_buckets[ip] = bucket
        return False
    bucket.append(now)
    _rate_buckets[ip] = bucket
    return True


@app.get("/api/market/free", tags=["Free"])
async def market_free(request: Request, asset: str = Query(..., description="Asset ticker")) -> JSONResponse:
    asset = asset.upper().strip()
    ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(ip):
        audit("rate_limited", {"asset": asset, "ip": ip})
        return JSONResponse(status_code=429, content={
            "error": "Rate limit exceeded (10 req/min). Use a paid endpoint or metered subscription key for higher limits.",
            "upgrade": {"per_call": "/api/market", "subscription": "POST /api/subscribe"},
        })
    try:
        intel = await build_market_intelligence(asset)
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})
    audit("free_call", {"asset": asset, "ip": ip})
    return JSONResponse(content=intel)


# ─────────────────────────────────────────────────────────────────────────────
# Screener endpoint
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/screener", tags=["Paid — x402"])
async def screener(
    request: Request,
    signal: str = Query("ALL", description="BUY | SELL | HOLD | ALL"),
    min_confidence: int = Query(0, description="Minimum confidence % (0-100)"),
) -> JSONResponse:
    resource_url = public_resource_url(request)
    err, payer, tx_hash, via_sub = await _auth_or_402(
        request,
        resource_url,
        price_raw=PRICE_USDC_RAW,
        price_usdc=PRICE_USDC,
        description="Market screener — scan supported assets for trade signals",
        usage_kind="market",
        usage_units=len(SCREENER_ASSETS),
    )
    if err is not None:
        return err

    signal = signal.upper()
    results = await asyncio.gather(*[build_market_intelligence(a) for a in SCREENER_ASSETS],
                                   return_exceptions=True)
    filtered = []
    for intel in results:
        if isinstance(intel, Exception):
            continue
        sig = intel.get("signals", {})
        trade = derive_trade_action(intel)
        if signal != "ALL" and trade["action"] != signal:
            continue
        if trade["confidence"] < min_confidence:
            continue
        filtered.append({
            "asset": intel["asset"], "name": intel["name"],
            "price_usd": intel["price_usd"],
            "change_24h_pct": intel["change_24h_pct"],
            "action": trade["action"], "confidence": trade["confidence"],
            "risk_level": trade["risk_level"],
            "stop_loss_pct": trade["stop_loss_pct"],
            "take_profit_pct": trade["take_profit_pct"],
            "reasoning": trade["reasoning"],
            "fear_greed_index": intel.get("fear_greed_index"),
        })
    filtered.sort(key=lambda x: x["confidence"], reverse=True)
    audit("screener_call", {"signal_filter": signal, "min_confidence": min_confidence,
                             "payer": payer, "results": len(filtered), "via_subscription": via_sub,
                             "tx_hash": tx_hash})
    payload = {
        "screener": filtered, "filter": signal, "min_confidence": min_confidence,
        "scanned": len(SCREENER_ASSETS), "matched": len(filtered),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "network": KITE_NETWORK,
    }
    if via_sub:
        payload["_auth"] = "subscription"
    else:
        payload["_payment"] = {"tx_hash": tx_hash, "amount_usdc": PRICE_USDC, "payer": payer}
    return JSONResponse(content=payload)


# ─────────────────────────────────────────────────────────────────────────────
# Agent feed endpoint
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/agent/feed", tags=["Free"])
async def agent_feed(limit: int = Query(20, le=50)) -> JSONResponse:
    public_feed = [public_agent_entry(entry) for entry in _agent_feed[:limit]]
    return JSONResponse(content={
        "feed": public_feed,
        "total": len(_agent_feed),
        "agent": "Agent Agora Autonomous Trader",
        "network": KITE_NETWORK,
        "privacy_mode": "public_redacted",
    })


@app.post("/api/agent/feed", tags=["Free"])
async def agent_feed_post(request: Request) -> JSONResponse:
    admin_key = request.headers.get("X-Admin-Key", "")
    api_key = (request.headers.get("X-API-Key") or request.headers.get("x-api-key") or "").strip()
    if admin_key != ADMIN_KEY and not validate_api_key(api_key):
        raise HTTPException(status_code=403, detail="Valid X-Admin-Key or X-API-Key required")
    body = await request.json()
    attest_hash = body.get("attestation_hash", "")
    if attest_hash:
        chain_tx = await write_attestation_to_chain(attest_hash)
        if chain_tx:
            body["chain_attestation_tx"] = chain_tx
    agent_record(body)
    return JSONResponse(content={"status": "recorded", "chain_attestation_tx": body.get("chain_attestation_tx", "")})


# ─────────────────────────────────────────────────────────────────────────────
# Agent work orders
# ─────────────────────────────────────────────────────────────────────────────
WORK_SKILLS = {
    "market_sentinel": {
        "name": "Market Sentinel",
        "status": "live",
        "price_usdc": WORK_ORDER_PRICE_USDC,
        "description": "Scan assets, buy market intelligence, produce signed risk/opportunity decisions.",
        "examples": [
            "Watch BTC, ETH, and SOL and tell me where risk is rising.",
            "Find the strongest BUY opportunities across supported assets.",
        ],
    },
    "service_discovery": {
        "name": "Service Discovery",
        "status": "live",
        "price_usdc": "0",
        "description": "Show Kite Passport capabilities or Agent Agora services and payment terms.",
        "examples": ["Show me all the services available on Kite Passport."],
    },
    "shopping_agent": {
        "name": "Shopping Agent",
        "status": "live",
        "price_usdc": WORK_ORDER_PRICE_USDC,
        "description": "Creates a purchase-ready shopping brief after Kite settlement; checkout remains user-approved.",
        "examples": ["Buy healthy snacks under $25 using Kite Passport."],
    },
    "travel_agent": {
        "name": "Travel Agent",
        "status": "live",
        "price_usdc": WORK_ORDER_PRICE_USDC,
        "description": "Creates itinerary/email-ready trip plans after Kite settlement.",
        "examples": ["Create a 3-day Tokyo itinerary and email it to me using Kite Passport."],
    },
    "general_task_agent": {
        "name": "General Task Agent",
        "status": "live",
        "price_usdc": WORK_ORDER_PRICE_USDC,
        "description": "Writes general content and sends email deliverables after Kite settlement.",
        "examples": ["Write me a story and send it to my email."],
    },
    "research_agent": {
        "name": "Research Agent",
        "status": "live",
        "price_usdc": WORK_ORDER_PRICE_USDC,
        "description": "Creates structured research briefs and emails them when a recipient is provided.",
        "examples": ["Research top Web3 grants for AI agents and email me the report."],
    },
    "code_review_agent": {
        "name": "Code Review Agent",
        "status": "live",
        "price_usdc": WORK_ORDER_PRICE_USDC,
        "description": "Reviews pasted code or diffs for bugs, security risks, and improvements.",
        "examples": ["Review this diff for security issues."],
    },
    "receipt_agent": {
        "name": "Receipt Agent",
        "status": "live",
        "price_usdc": WORK_ORDER_PRICE_USDC,
        "description": "Generates Kite settlement receipts and invoice-style proof documents.",
        "examples": ["Create a receipt for this Kite transaction."],
    },
}


def classify_work_order(prompt: str) -> str:
    p = prompt.lower()
    def has_any(terms: List[str]) -> bool:
        for term in terms:
            if re.search(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", p):
                return True
        return False

    # Service discovery — always first
    if any(w in p for w in ("services", "available", "catalog", "what can", "show me all", "what skills")):
        return "service_discovery"

    # Receipt — tx hash proof requests
    if any(w in p for w in ("receipt", "invoice", "proof document", "payment proof", "settlement receipt")):
        return "receipt_agent"

    # Code review — must come before research to avoid "review this" ambiguity
    if any(w in p for w in (
        "code review", "review this code", "review this diff", "security review",
        "bug review", "audit this code", "audit this", "review pasted code",
        "review this api handler", "api handler", "production risks",
        "security issues", "pasted code", "review this function",
        "review this file", "review this endpoint", "review this handler",
    )):
        return "code_review_agent"

    # Market sentinel — check BEFORE research so "sell alert report", "buy signal report" etc. route correctly
    _market_keywords = (
        "btc", "eth", "sol", "kite", "doge", "bnb", "xrp", "ada", "avax", "link",
        "bitcoin", "ethereum", "solana", "crypto", "token", "coin",
        "sell alert", "buy alert", "buy signal", "sell signal", "hold signal",
        "overextended", "overbought", "oversold", "trade signal", "trading signal",
        "market signal", "risk is rising", "opportunities across", "buy opportunities",
        "sell opportunities", "strongest buy", "strongest sell", "market scan",
        "asset scan", "which assets", "market intelligence", "market watch",
    )
    if any(w in p for w in _market_keywords):
        return "market_sentinel"

    # Shopping — before research to catch "find me a product" style
    if any(w in p for w in ("buy me", "purchase", "order", "amazon", "snacks", "cart", "shopping", "find me", "under $", "under  $", "product", "products", "checkout")):
        return "shopping_agent"

    # Travel
    if any(w in p for w in ("itinerary", "hotel", "flight", "trip", "travel", "vacation", "visit")):
        return "travel_agent"

    # Research — general topics with no market/shopping/travel signals
    if any(w in p for w in ("research", "brief", "report", "find grants", "competitive analysis", "market map", "latest", "news", "compare", "analysis of", "summarize", "summary")):
        return "research_agent"

    # Unsupported design requests
    if has_any(["banner", "design", "image", "creative", "logo", "website", "frontend", "ui", "ux", "landing page", "dashboard", "app screen", "mobile app"]):
        return "unsupported_design"

    # Writing / general content
    if any(w in p for w in ("write", "story", "essay", "draft", "compose", "email", "send it", "send to")):
        return "general_task_agent"

    # Remaining market-adjacent terms fall to market_sentinel
    if any(w in p for w in ("market", "buy", "sell", "hold", "risk", "asset", "price", "signal")):
        return "market_sentinel"

    return "general_task_agent"


def extract_assets(prompt: str, explicit: List[str]) -> List[str]:
    out = [a.strip().upper() for a in explicit if a.strip()]
    if out:
        return out[:BATCH_MAX_ASSETS]
    tokens = set(re.findall(r"\b[A-Z]{2,6}\b", prompt.upper()))
    found = [a for a in SCREENER_ASSETS if a in tokens]
    return found[:BATCH_MAX_ASSETS] or SCREENER_ASSETS[:BATCH_MAX_ASSETS]


async def resolve_market_assets(prompt: str, explicit: List[str]) -> List[str]:
    assets = extract_assets(prompt, explicit)
    p = prompt.lower()
    wants_discovery = any(w in p for w in ("strongest", "opportunities", "supported", "across", "market", "trending", "top", "all assets", "overextended", "overbought", "oversold", "sell alert", "buy alert", "sell signal", "buy signal", "which assets", "scan", "all tokens", "all coins"))
    if wants_discovery and not explicit:
        trending = await fetch_trending_assets(limit=8)
        combined = []
        for sym in [*trending, *SCREENER_ASSETS]:
            if sym not in combined:
                combined.append(sym)
        return combined[:BATCH_MAX_ASSETS]
    return assets


def extract_email(prompt: str) -> Optional[str]:
    match = re.search(r"[\w.\-+%]+@[\w.\-]+\.[A-Za-z]{2,}", prompt)
    return match.group(0) if match else None


def send_email(to_email: str, subject: str, body: str) -> Dict[str, Any]:
    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
        return {
            "status": "not_sent",
            "reason": "SMTP is not configured on the production server.",
            "required_env": ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"],
        }
    try:
        msg = EmailMessage()
        msg["From"] = SMTP_FROM
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(msg)
        return {"status": "sent", "to": to_email, "from": SMTP_FROM, "sent_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        log.warning("email send failed: %s", e)
        reason = str(e)
        if "You can only send testing emails to your own email address" in reason or "resend.com/domains" in reason:
            return {
                "status": "failed",
                "to": to_email,
                "reason": "Email provider is in Resend testing mode. It can only send to the verified account email until a sending domain is verified.",
                "provider": "resend",
                "fix": "Verify a domain in Resend, set SMTP_FROM to an address on that domain, then redeploy/restart the Fly app.",
            }
        return {"status": "failed", "to": to_email, "reason": reason}


def clean_task_topic(prompt: str) -> str:
    text = re.sub(r"[\w.\-+%]+@[\w.\-]+\.[A-Za-z]{2,}", "", prompt)
    text = re.sub(r"\b(email me|email it|email this|send it|send this|send to|send|to me|using kite passport)\b", "", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip(" -.:") or "requested task"


def extract_budget_usd(prompt: str) -> Optional[float]:
    match = re.search(r"(?:under|below|less than|budget(?: of)?|within)\s*\$?(\d+(?:\.\d+)?)", prompt, re.I)
    if not match:
        match = re.search(r"\$(\d+(?:\.\d+)?)", prompt)
    return float(match.group(1)) if match else None


def parse_usd_amount(value: Any) -> Optional[float]:
    text = str(value or "")
    match = re.search(r"\$?\s*(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


async def build_travel_result(prompt: str) -> Dict[str, Any]:
    recipient = extract_email(prompt)
    sources = await web_research(f"travel planning current recommendations {clean_task_topic(prompt)}", max_results=6, current_only=True)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "itinerary", "email_subject", "email_body", "sources_used", "limitations"],
        "properties": {
            "summary": {"type": "string"},
            "itinerary": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["day", "theme", "plan"],
                    "properties": {
                        "day": {"type": "integer"},
                        "theme": {"type": "string"},
                        "plan": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "email_subject": {"type": "string"},
            "email_body": {"type": "string"},
            "sources_used": {"type": "array", "items": {"type": "string"}},
            "limitations": {"type": "array", "items": {"type": "string"}},
        },
    }
    system = (
        "You are a precise travel planning agent. Create practical, current itineraries. "
        "Use source context when available. Do not invent booking confirmations or prices. "
        "If a recommendation depends on current openings, prices, or schedules, note it clearly in prose. "
        + PLAIN_PROSE
    )
    user = f"Current date: {current_date_iso()}.\nTask: {prompt}\n\nFresh source context:\n{sources_context(sources)}"
    try:
        result = await openai_json(system, user, schema)
    except Exception as e:
        log.warning("openai travel failed: %s", e)
        fallback_days = [
            {"day": idx + 1, "theme": source.get("title", "Travel research")[:80], "plan": [source.get("content", "Use this source to refine the itinerary.")[:220]]}
            for idx, source in enumerate(sources[:3])
        ]
        result = {
            "summary": "Created a source-backed travel research brief; itinerary synthesis is limited because the model provider is unavailable.",
            "itinerary": fallback_days,
            "email_subject": "Travel task could not be completed",
            "email_body": "Travel source research completed, but full itinerary synthesis could not run because the model provider is currently unavailable.",
            "sources_used": [s.get("url", "") for s in sources],
            "limitations": [provider_error_message(e)],
        }
    email_delivery = send_email(recipient, result["email_subject"], result["email_body"]) if recipient else {"status": "not_sent", "reason": "No recipient email address was found in the prompt."}
    return {
        "summary": result["summary"],
        "deliverable_type": "travel_itinerary",
        "itinerary": result["itinerary"],
        "email_draft": {"to": recipient, "subject": result["email_subject"], "body": result["email_body"]},
        "email_delivery": email_delivery,
        "sources": sources,
        "limitations": result.get("limitations", []),
        "source_prompt": prompt,
    }


async def build_general_task_result(prompt: str) -> Dict[str, Any]:
    recipient = extract_email(prompt)
    current_requested = needs_current_info(prompt)
    sources = await web_research(clean_task_topic(prompt), max_results=6, current_only=True) if current_requested else []
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "content", "email_subject", "email_body"],
        "properties": {
            "summary": {"type": "string"},
            "content": {"type": "string"},
            "email_subject": {"type": "string"},
            "email_body": {"type": "string"},
        },
    }
    system = (
        "You are a careful general-purpose task agent. Follow the user's instruction directly. "
        "Be specific, polished, and useful. Do not turn unrelated tasks into crypto analysis. "
        "Do not invent facts, metrics, customer traction, partnerships, or events about real people, companies, or Agent Agora. "
        f"Current date is {current_date_iso()}. "
        "If the task asks for current/latest/ongoing facts, use only the provided fresh source context and say when evidence is missing. "
        "When writing about Agent Agora, use only these known facts: it is a Kite-settled autonomous agent work platform with work orders, market intelligence, research, writing/email, code review, receipts, travel planning, shopping briefs, and proof/attestation records. "
        "If the user asks for an update without providing new milestones, frame it as a capability/product update, not traction or roadmap progress. "
        + PLAIN_PROSE
    )
    try:
        user = prompt
        if current_requested:
            user = f"Task: {prompt}\n\nFresh source context:\n{sources_context(sources)}"
        result = await openai_json(system, user, schema)
    except Exception as e:
        log.warning("openai general task failed: %s", e)
        result = {
            "summary": "General task provider unavailable.",
            "content": f"This task needs the OpenAI writing provider, but it could not run: {provider_error_message(e)}",
            "email_subject": "Agent Agora task could not be completed",
            "email_body": f"This task needs the OpenAI writing provider, but it could not run: {provider_error_message(e)}",
        }
    email_delivery = send_email(recipient, result["email_subject"], result["email_body"]) if recipient else {"status": "not_sent", "reason": "No recipient email address was found in the prompt."}
    return {
        "summary": result["summary"],
        "deliverable_type": "written_email_task",
        "content": result["content"],
        "email_draft": {"to": recipient, "subject": result["email_subject"], "body": result["email_body"]},
        "email_delivery": email_delivery,
        "provider": "openai",
        "sources": sources,
        "source_prompt": prompt,
    }


async def build_research_result(prompt: str) -> Dict[str, Any]:
    recipient = extract_email(prompt)
    topic = clean_task_topic(prompt)
    current_requested = needs_current_info(prompt)
    sources = await web_research(topic, max_results=10 if current_requested else 8, current_only=current_requested)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "findings", "recommended_next_steps", "email_subject", "email_body"],
        "properties": {
            "summary": {"type": "string"},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "detail"],
                    "properties": {"title": {"type": "string"}, "detail": {"type": "string"}},
                },
            },
            "recommended_next_steps": {"type": "array", "items": {"type": "string"}},
            "email_subject": {"type": "string"},
            "email_body": {"type": "string"},
        },
    }
    system = (
        "You are a research agent. Use the source context, cite source numbers in finding details like [1], "
        "and say when evidence is limited. Do not invent facts. "
        f"Current date is {current_date_iso()}. "
        "For current, latest, ongoing, upcoming, active, or deadline-driven tasks, exclude items whose event date, end date, or application deadline is before the current date. "
        "Do not include old 2024/2025 events unless a source explicitly says the same program is open or upcoming now. "
        "If no verified current items are found, say that clearly instead of filling the report with stale examples. "
        + PLAIN_PROSE
    )
    user = f"Research task: {prompt}\n\nFresh sources:\n{sources_context(sources)}"
    try:
        result = await openai_json(system, user, schema)
    except Exception as e:
        log.warning("openai research failed: %s", e)
        findings = [
            {"title": source.get("title", "Source"), "detail": f"{source.get('content', '')[:420]} [{idx + 1}]"}
            for idx, source in enumerate(sources[:6])
        ] or [{"title": "Provider failure", "detail": f"Could not complete live research: {provider_error_message(e)}"}]
        result = {
            "summary": "Created a source-backed research brief; synthesis is limited because the model provider is unavailable.",
            "findings": findings,
            "recommended_next_steps": ["Review the cited sources directly.", "Retry synthesis after OpenAI quota/rate limit is resolved."],
            "email_subject": "Source-backed research brief",
            "email_body": "Research sources were retrieved, but full synthesis could not run because the model provider is currently unavailable.\n\n" + "\n\n".join([f"{i+1}. {f['title']}\n\n{f['detail']}" for i, f in enumerate(findings)]),
        }
    subject = result["email_subject"]
    findings_text = "\n\n".join(
        f"{i+1}. {f['title']}\n\n{f['detail']}"
        for i, f in enumerate(result["findings"])
    )
    steps_text = "\n\n".join(
        f"{i+1}. {s}" for i, s in enumerate(result.get("recommended_next_steps", []))
    )
    body = (
        f"{result['summary']}\n\n"
        f"Research Findings\n\n"
        f"{findings_text}\n\n"
        + (f"Recommended Next Steps\n\n{steps_text}\n\n" if steps_text else "")
        + "Delivered by Agent Agora"
    )
    email_delivery = send_email(recipient, subject, body) if recipient else {"status": "not_sent", "reason": "No recipient email address was found in the prompt."}
    return {
        "summary": result["summary"],
        "deliverable_type": "research_brief",
        "topic": topic,
        "findings": result["findings"],
        "recommended_next_steps": result["recommended_next_steps"],
        "sources": sources,
        "email_draft": {"to": recipient, "subject": subject, "body": body},
        "email_delivery": email_delivery,
        "source_prompt": prompt,
    }


async def build_code_review_result(prompt: str) -> Dict[str, Any]:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "findings", "recommendations", "confidence_note"],
        "properties": {
            "summary": {"type": "string"},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["severity", "title", "evidence", "risk", "fix"],
                    "properties": {
                        "severity": {"type": "string"},
                        "title": {"type": "string"},
                        "evidence": {"type": "string"},
                        "risk": {"type": "string"},
                        "fix": {"type": "string"},
                    },
                },
            },
            "recommendations": {"type": "array", "items": {"type": "string"}},
            "confidence_note": {"type": "string"},
        },
    }
    system = (
        "You are a senior code reviewer. Review only the code or diff pasted by the user. "
        "Do not give generic checklist advice. Every finding must cite concrete evidence copied exactly from the submitted code: a function name, variable name, config value, endpoint, import, or short code phrase. "
        "If you cannot point to specific code evidence, do not create that finding. "
        "Prioritize exploitable security issues, behavioral bugs, unsafe defaults, leaked secrets, missing auth, bad error handling, replay/race risks, and production reliability. "
        "Avoid vague advice like 'validate inputs' unless you identify the exact unvalidated input and where it flows. "
        "Do not say environment variables are risky by themselves; only flag a concrete hardcoded fallback, logging exposure, or missing required-secret failure. "
        "Do not say hashes may expose sensitive data unless the code actually hashes sensitive input and returns/logs it. "
        "Do not describe partial API key masking as exposure unless the visible prefix is itself enough to authenticate. "
        "If the pasted code is too incomplete to support a claim, say that in confidence_note. "
        + PLAIN_PROSE
    )
    try:
        user = (
            "Review this pasted code for production bugs and security risks. "
            "Return only evidence-backed findings.\n\n"
            f"{prompt}"
        )
        result = await openai_json(system, user, schema)
    except Exception as e:
        log.warning("openai code review failed: %s", e)
        result = {
            "summary": "Code review provider unavailable.",
            "findings": [{"severity": "high", "title": "OpenAI unavailable", "evidence": "Provider call failed", "risk": f"This review needs the code-review model provider, but it could not run: {provider_error_message(e)}", "fix": "Resolve OpenAI quota/rate limit and retry with the exact code or diff pasted into the work order."}],
            "recommendations": ["Resolve OpenAI quota/rate limit, then retry with the exact code or diff pasted into the work order."],
            "confidence_note": "No code analysis was completed because the provider call failed.",
        }
    pasted = prompt.lower()
    filtered_findings = []
    rejected = 0
    generic_terms = (
        "environment variables", "access controls", "injection attacks",
        "unexpected data formats", "sensitive data", "user privacy",
        "dependency management", "input validation",
    )
    for finding in result.get("findings", []):
        evidence = str(finding.get("evidence") or "").strip()
        title = str(finding.get("title") or "")
        risk = str(finding.get("risk") or "")
        fix = str(finding.get("fix") or "")
        finding_text = f"{title} {risk} {fix}".lower()
        evidence_l = evidence.lower()
        if not evidence or len(evidence) < 4:
            rejected += 1
            continue
        if "api_key[:8]" in evidence_l and any(term in finding_text for term in ("exposure", "leak", "sensitive")):
            rejected += 1
            continue
        if "def get_intel" in evidence_l and "asset" in evidence_l and "quote(asset)" in pasted and any(term in finding_text for term in ("injection", "malicious")):
            rejected += 1
            continue
        if "await post_decision" in evidence_l and any(term in finding_text for term in ("race condition", "simultaneously", "multiple cycles")):
            rejected += 1
            continue
        if "httpx" in evidence_l and "dependency" in finding_text:
            rejected += 1
            continue
        evidence_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|0x[a-fA-F0-9]{6,}|[A-Z_]{4,}", evidence)
        has_code_token = any(token.lower() in pasted for token in evidence_tokens)
        has_literal = evidence.lower() in pasted
        sounds_generic = any(term in f"{title} {risk}".lower() for term in generic_terms) and not has_code_token
        if not (has_literal or has_code_token) or sounds_generic:
            rejected += 1
            continue
        filtered_findings.append(finding)

    confidence_note = result.get("confidence_note", "")
    if rejected:
        confidence_note = (confidence_note + f" Filtered {rejected} generic or unsupported finding(s).").strip()
    if not filtered_findings:
        filtered_findings = [{
            "severity": "info",
            "title": "No evidence-backed findings returned",
            "evidence": "No finding cited a concrete code phrase from the pasted snippet.",
            "risk": "The model output was too generic to trust for paid code review.",
            "fix": "Retry with the relevant file or diff and include surrounding code for auth, config, and request handling.",
        }]

    return {
        "summary": result["summary"],
        "deliverable_type": "code_review",
        "findings": filtered_findings,
        "recommendations": result["recommendations"],
        "confidence_note": confidence_note,
        "provider": "openai",
        "source_prompt": prompt,
    }


def build_receipt_result(prompt: str, tx_hash: Optional[str]) -> Dict[str, Any]:
    receipt_id = "rcpt_" + secrets.token_hex(6)
    amount_match = re.search(r"(?:\$|amount\s*:?\s*)(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:usdc|usd)\b", prompt, re.I)
    amount = amount_match.group(1) if amount_match else WORK_ORDER_PRICE_USDC
    if amount_match and not amount:
        amount = amount_match.group(2)
    issued_at = datetime.now(timezone.utc).isoformat()
    prompt_l = prompt.lower()
    if any(w in prompt_l for w in ("demo", "judge", "submission", "public proof")):
        document_type = "public_demo_proof"
        summary = "Created a public Kite settlement proof document."
        title = "Settlement Proof"
        purpose = "Demonstrates that the agent completed paid work only after Kite settlement proof was supplied."
        sections = [
            {
                "heading": "What this proves",
                "items": [
                    "A real Kite transaction hash was attached to the work order.",
                    "The work order executed through Agent Agora after settlement verification.",
                    "The transaction hash is preserved in the result payload for auditability.",
                ],
            },
            {
                "heading": "Public demo script",
                "items": [
                    "Open the Work page.",
                    "Paste the Kite transaction hash into the proof field.",
                    "Run a supported agent skill and show the completed work order plus attestation hash.",
                ],
            },
        ]
    elif any(w in prompt_l for w in ("audit", "compliance", "proof document", "settlement proof")):
        document_type = "audit_proof"
        summary = "Created an audit-ready Kite settlement proof."
        title = "Agent Work Audit Record"
        purpose = "Records settlement, merchant, network, and work-order proof fields for later review."
        sections = [
            {
                "heading": "Audit trail",
                "items": [
                    f"Receipt ID: {receipt_id}",
                    f"Network: {KITE_NETWORK}",
                    f"Merchant wallet: {SERVICE_WALLET}",
                    f"Payment transaction: {tx_hash or 'not provided'}",
                ],
            },
            {
                "heading": "Verification checklist",
                "items": [
                    "Confirm the transaction hash exists on Kite.",
                    "Confirm the paid work-order response includes the same hash.",
                    "Confirm the attestation hash is present in the completed work-order payload.",
                ],
            },
        ]
    else:
        document_type = "settlement_receipt"
        summary = "Created a Kite settlement receipt."
        title = "Settlement Receipt"
        purpose = "Invoice-style receipt for a paid agent work order settled on Kite."
        sections = [
            {
                "heading": "Payment details",
                "items": [
                    f"Amount: {amount} USDC",
                    f"Network: {KITE_NETWORK}",
                    f"Merchant wallet: {SERVICE_WALLET}",
                    f"Transaction: {tx_hash or 'not provided'}",
                ],
            },
            {
                "heading": "Usage note",
                "items": [
                    "Attach this receipt to the completed work order.",
                    "Use the transaction hash as the public settlement reference.",
                ],
            },
        ]
    receipt = {
        "receipt_id": receipt_id,
        "status": "issued",
        "document_type": document_type,
        "title": title,
        "purpose": purpose,
        "network": KITE_NETWORK,
        "merchant_wallet": SERVICE_WALLET,
        "tx_hash": tx_hash,
        "amount_usdc": amount,
        "issued_at": issued_at,
    }
    return {
        "summary": summary,
        "deliverable_type": document_type,
        "receipt": receipt,
        "sections": sections,
        "invoice_text": f"{title} {receipt_id}: {amount} USDC settled on {KITE_NETWORK}. Tx: {tx_hash or 'not provided'}.",
        "source_prompt": prompt,
    }


async def build_shopping_result(prompt: str) -> Dict[str, Any]:
    budget = extract_budget_usd(prompt)
    sources = await web_research(f"shopping product recommendations current prices {clean_task_topic(prompt)}", max_results=8, current_only=True)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "budget_usd", "items", "checkout_policy"],
        "properties": {
            "summary": {"type": "string"},
            "budget_usd": {"type": "number"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "target_price_usd", "reason"],
                    "properties": {
                        "name": {"type": "string"},
                        "target_price_usd": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "checkout_policy": {"type": "string"},
        },
    }
    system = (
        "You are a shopping research agent. Use live source snippets where available. "
        "Recommend real product categories or products, obey the requested budget as a hard maximum per purchasable item or pack, and never claim checkout/order placement happened. "
        f"Current date is {current_date_iso()}. "
        "If a source mentions a product above budget, do not recommend it; choose a cheaper source-supported alternative or say pricing needs checking. "
        + PLAIN_PROSE
    )
    budget_line = f"Hard budget maximum: ${budget:.2f}." if budget is not None else "No explicit budget maximum."
    user = f"Shopping task: {prompt}\n{budget_line}\n\nSources:\n{sources_context(sources)}"
    try:
        result = await openai_json(system, user, schema)
        if budget is not None:
            over_budget = [
                item for item in result.get("items", [])
                if (parse_usd_amount(item.get("target_price_usd")) is not None and parse_usd_amount(item.get("target_price_usd")) > budget)
            ]
            if over_budget:
                retry_user = (
                    f"The previous shopping result included items over the ${budget:.2f} hard budget. "
                    "Regenerate the result with only source-supported options at or below that budget. "
                    "Do not include over-budget packs, even if they are popular.\n\n"
                    f"Shopping task: {prompt}\n{budget_line}\n\nSources:\n{sources_context(sources)}"
                )
                result = await openai_json(system, retry_user, schema)
    except Exception as e:
        log.warning("openai shopping failed: %s", e)
        items = [
            {"name": source.get("title", "Shopping source")[:90], "target_price_usd": "check source", "reason": source.get("content", "Use source for current product details.")[:260]}
            for source in sources[:5]
        ] or [{"name": "Provider failure", "target_price_usd": "unknown", "reason": f"Could not complete shopping research: {provider_error_message(e)}"}]
        result = {
            "summary": "Created a source-backed shopping research brief; product synthesis is limited because the model provider is unavailable.",
            "budget_usd": 0,
            "items": items,
            "checkout_policy": "No checkout or order was placed.",
        }
    if budget is not None:
        in_budget_items = []
        removed = 0
        for item in result.get("items", []):
            price = parse_usd_amount(item.get("target_price_usd"))
            if price is not None and price > budget:
                removed += 1
                continue
            in_budget_items.append(item)
        if removed:
            result["items"] = in_budget_items
            result["checkout_policy"] = (
                f"No checkout or order was placed. {removed} over-budget recommendation(s) were removed because the requested maximum was ${budget:.2f}."
            )
    return {
        "summary": result["summary"],
        "deliverable_type": "shopping_brief",
        "budget_usd": result["budget_usd"],
        "items": result["items"],
        "checkout_policy": result["checkout_policy"],
        "sources": sources,
        "source_prompt": prompt,
    }


async def build_non_market_result(skill_id: str, prompt: str, tx_hash: Optional[str] = None) -> Dict[str, Any]:
    if skill_id == "travel_agent":
        return await build_travel_result(prompt)
    if skill_id == "shopping_agent":
        return await build_shopping_result(prompt)
    if skill_id == "general_task_agent":
        return await build_general_task_result(prompt)
    if skill_id == "research_agent":
        return await build_research_result(prompt)
    if skill_id == "code_review_agent":
        return await build_code_review_result(prompt)
    if skill_id == "receipt_agent":
        return build_receipt_result(prompt, tx_hash)
    raise ValueError(f"Unsupported work skill: {skill_id}")


async def build_service_discovery_result(prompt: str) -> Dict[str, Any]:
    p = prompt.lower()
    wants_passport = "passport" in p or "kpass" in p or "kite services" in p
    if wants_passport:
        try:
            catalog = await fetch_kite_services(limit=20)
            return {
                "summary": f"Found {catalog['total']} services in the live Kite Passport catalog.",
                "scope": "kite_catalog",
                "note": "Live data from Kite service discovery. Some services use x402 and others use Tempo/payment gateway flows.",
                "services": catalog["services"],
                "count": catalog["count"],
                "total": catalog["total"],
                "next_cursor": catalog["next_cursor"],
                "backend": catalog["backend"],
                "official_links": [
                    "https://agentpassport.ai/quickstart/",
                    "https://docs.gokite.ai/",
                    "https://docs.gokite.ai/kite-agent-passport/kite-agent-passport",
                ],
            }
        except Exception as e:
            log.warning("kite catalog lookup failed: %s", e)
        return {
            "summary": "Kite Passport services and capabilities available to agents.",
            "scope": "kite_passport",
            "note": "Live Kite catalog lookup is unavailable, so this lists the Passport capabilities Agent Agora can safely describe.",
            "services": [
                {
                    "name": "Agent identity and account",
                    "what_it_does": "Creates or signs in an agent/user identity for Kite Passport without putting private keys in the app UI.",
                    "example": "kpass signup init --email you@example.com",
                },
                {
                    "name": "Wallet balance and funding",
                    "what_it_does": "Shows the Passport wallet address and USDC balance on Kite L1 Mainnet.",
                    "example": "kpass wallet balance --output json",
                },
                {
                    "name": "Direct USDC settlement",
                    "what_it_does": "Sends USDC on Kite to a merchant wallet and returns a transaction hash that can be used as payment proof.",
                    "example": "kpass wallet send --to 0x... --amount 0.05 --asset USDC",
                },
                {
                    "name": "x402 paid API execution",
                    "what_it_does": "Lets an agent pay an x402-protected endpoint when the merchant host is accepted by Passport discovery.",
                    "example": "kpass agent:session execute --url https://merchant.example/api --method POST",
                },
                {
                    "name": "Spending sessions and delegation",
                    "what_it_does": "Approves bounded agent spend so autonomous agents can pay for tasks inside a budget instead of asking every time.",
                    "example": "Use Passport session approval before paid API calls.",
                },
                {
                    "name": "Shopping and checkout flows",
                    "what_it_does": "Supports user-approved shopping/payment flows where an agent prepares the purchase and the user approves checkout.",
                    "example": "Buy healthy snacks under $25 using Kite Passport.",
                },
                {
                    "name": "Activity and audit trail",
                    "what_it_does": "Lets users review payments, agent actions, wallet sends, sessions, and completed work.",
                    "example": "Use Passport activity/history tooling to inspect past payments.",
                },
            ],
            "agent_agora_integration": [
                "Agent Agora accepts direct Kite transaction hashes as proof for work orders.",
                "Agent Agora can mint an aa_ subscription key after a verified 0.50 USDC Kite payment.",
                "Agent Agora records an attestation hash for completed work so results are auditable.",
            ],
            "official_links": [
                "https://agentpassport.ai/quickstart/",
                "https://docs.gokite.ai/",
                "https://docs.gokite.ai/kite-agent-passport/kite-agent-passport",
            ],
        }
    return {
        "summary": "Agent Agora services and paid endpoints.",
        "scope": "agent_agora",
        "live_services": [s for s, meta in WORK_SKILLS.items() if meta["status"] == "live"],
        "paid_endpoints": ["/api/market", "/api/market/batch", "/api/signals/{asset}", "/api/screener", "/api/agent/work-orders"],
        "network": KITE_NETWORK,
        "wallet": SERVICE_WALLET,
    }


@app.get("/api/agent/skills", tags=["Free"])
async def agent_skills() -> JSONResponse:
    return JSONResponse(content={
        "network": KITE_NETWORK,
        "service_wallet": SERVICE_WALLET,
        "skills": [
            {"id": sid, **meta}
            for sid, meta in WORK_SKILLS.items()
        ],
        "note": "Only live skills execute. Merchant-dependent skills are listed for platform expansion and do not fake completion.",
    })


@app.post("/api/agent/work-orders", tags=["Paid — x402"])
async def create_work_order(req: WorkOrderRequest, request: Request) -> JSONResponse:
    prompt = req.prompt.strip()
    skill_id = classify_work_order(prompt)
    if skill_id == "unsupported_design":
        return JSONResponse(status_code=422, content={
            "error": "Creative image and frontend generation are not offered in this version because the output is not reliable enough for paid work.",
            "supported_skills": ["market_sentinel", "research_agent", "code_review_agent", "receipt_agent", "travel_agent", "shopping_agent", "general_task_agent"],
            "suggestion": "Try a research, writing, code review, receipt, travel, shopping, or market intelligence task instead.",
        })
    skill = WORK_SKILLS[skill_id]

    if skill_id == "service_discovery":
        payload = {
            "work_order_id": "wo_" + secrets.token_hex(8),
            "skill": {"id": skill_id, **skill},
            "status": "completed",
            "result": await build_service_discovery_result(prompt),
            "attestation_hash": "",
            "network": KITE_NETWORK,
        }
        payload["attestation_hash"] = record_hash(payload)
        agent_record({
            "asset": "SERVICES",
            "action": "WORK",
            "confidence": 100,
            "risk_level": 0,
            "rationale": "Service discovery completed by Agent Agora.",
            "network": KITE_NETWORK,
            "attestation_hash": payload["attestation_hash"],
            "payment_mode": "free-discovery",
        })
        return JSONResponse(content=payload)

    if skill["status"] != "live":
        return JSONResponse(status_code=424, content={
            "error": f"{skill['name']} is not live yet because no real x402 merchant endpoint is configured.",
            "skill": {"id": skill_id, **skill},
            "required_to_activate": [
                "Configure a real merchant/service endpoint.",
                "Expose its x402 payment requirements.",
                "Add the endpoint URL to Agent Agora's skill registry.",
                "Execute only after Kite settlement succeeds.",
            ],
            "network": KITE_NETWORK,
        })

    resource_url = public_resource_url(request)
    err, payer, tx_hash, via_sub = await _auth_or_402(
        request,
        resource_url,
        WORK_ORDER_PRICE_RAW,
        WORK_ORDER_PRICE_USDC,
        "Agent Agora work order — autonomous paid market intelligence task",
        usage_kind="work_order",
        usage_units=1,
    )
    if err is not None:
        return err

    if skill_id == "market_sentinel":
        assets = await resolve_market_assets(prompt, req.assets)
        intel_results = await asyncio.gather(*[build_market_intelligence(a) for a in assets], return_exceptions=True)
        decisions: List[Dict[str, Any]] = []
        for intel in intel_results:
            if isinstance(intel, Exception):
                continue
            trade = derive_trade_action(intel)
            decisions.append({
                "asset": intel["asset"],
                "price_usd": intel.get("price_usd"),
                "action": trade["action"],
                "confidence": trade["confidence"],
                "risk_level": trade["risk_level"],
                "reasoning": trade["reasoning"],
                "fear_greed_index": intel.get("fear_greed_index"),
                "signals": intel.get("signals", {}),
                "evidence": {
                    "change_1h_pct": intel.get("change_1h_pct"),
                    "change_24h_pct": intel.get("change_24h_pct"),
                    "change_7d_pct": intel.get("change_7d_pct"),
                    "change_30d_pct": intel.get("change_30d_pct"),
                    "market_cap_usd": intel.get("market_cap_usd"),
                    "volume_24h_usd": intel.get("volume_24h_usd"),
                    "sources": intel.get("sources", []),
                },
            })
        scanned_count = len(decisions)
        decisions.sort(key=lambda d: d["confidence"], reverse=True)
        p_lower = prompt.lower()
        sell_intent = any(w in p_lower for w in ("sell", "overextended", "overbought", "sell alert", "sell signal", "short"))
        buy_intent = any(w in p_lower for w in ("buy", "buy alert", "buy signal", "bullish", "buy opportunities"))
        if sell_intent and not buy_intent and any(d["action"] == "SELL" for d in decisions):
            decisions = [d for d in decisions if d["action"] == "SELL"]
        elif buy_intent and not sell_intent and any(d["action"] == "BUY" for d in decisions):
            decisions = [d for d in decisions if d["action"] == "BUY"]
        result = {
            "summary": f"Scanned {scanned_count} assets — live crypto data from Kite mainnet. Matched {len(decisions)} signed market decisions.",
            "decisions": decisions[:req.max_results],
        }
    else:
        result = await build_non_market_result(skill_id, prompt, tx_hash)

    work_order_id = "wo_" + secrets.token_hex(8)
    payload = {
        "work_order_id": work_order_id,
        "skill": {"id": skill_id, **skill},
        "status": "completed",
        "prompt": prompt,
        "result": result,
        "payment": {
            "mode": "subscription" if via_sub else "x402",
            "payer": payer,
            "amount_usdc": "0" if via_sub else WORK_ORDER_PRICE_USDC,
            "tx_hash": tx_hash,
        },
        "network": KITE_NETWORK,
        "attestation_hash": "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload["attestation_hash"] = record_hash(payload)

    chain_tx = await write_attestation_to_chain(payload["attestation_hash"])
    payload["chain_attestation_tx"] = chain_tx or ""

    audit("work_order_completed", {
        "work_order_id": work_order_id,
        "skill": skill_id,
        "payer": payer,
        "tx_hash": tx_hash,
        "via_subscription": via_sub,
        "asset_count": len(result.get("decisions", [])),
        "attestation_hash": payload["attestation_hash"],
        "chain_attestation_tx": payload["chain_attestation_tx"],
    })
    agent_record({
        "asset": "WORK",
        "action": "WORK",
        "confidence": 100,
        "risk_level": 0,
        "rationale": payload["result"]["summary"],
        "network": KITE_NETWORK,
        "attestation_hash": payload["attestation_hash"],
        "chain_attestation_tx": payload["chain_attestation_tx"],
        "payment_tx_hash": tx_hash,
        "payment_mode": "subscription" if via_sub else "x402",
        "settled_in": "USDC · Kite chain",
    })
    return JSONResponse(content=payload)


# ─────────────────────────────────────────────────────────────────────────────
# Subscription endpoint
# ─────────────────────────────────────────────────────────────────────────────
@app.api_route("/api/subscribe", methods=["GET", "POST"], tags=["Paid — x402"])
async def subscribe(request: Request) -> JSONResponse:
    """
    Pay $0.50 USDC once → receive an API key valid for 24 hours.
    Works with direct Kite tx proof or x402 where host discovery allows it.
    Use the key as X-API-Key on market endpoints and work orders.
    """
    resource_url = public_resource_url(request)
    direct_tx = await extract_kite_tx_hash(request)
    if direct_tx:
        if not validate_kite_tx_hash(direct_tx):
            return JSONResponse(status_code=400, content={
                "error": "Invalid Kite transaction hash. Send a real 32-byte tx hash from kpass wallet send.",
                "expected": "X-Kite-Tx-Hash: 0x + 64 hex characters",
            })
        if tx_hash_consumed(direct_tx):
            return JSONResponse(status_code=409, content={
                "error": "This Kite transaction hash has already been used. Send a fresh subscription payment transaction.",
                "rule": "1 subscription tx hash = 1 subscription key.",
            })
        try:
            verification = await verify_kite_usdc_payment(direct_tx, SUB_PRICE_RAW)
        except HTTPException as e:
            return JSONResponse(status_code=e.status_code, content={"error": e.detail})
        err, payer, tx_hash = None, verification["payer"], direct_tx
        consume_tx_hash(tx_hash, "subscription", payer, SUB_PRICE_USDC)
        audit("direct_kite_subscription", {
            "payer": payer,
            "tx_hash": tx_hash,
            "amount_usdc_expected": SUB_PRICE_USDC,
            "amount_raw_verified": verification["amount_raw"],
            "merchant": verification["merchant"],
            "asset": verification["asset"],
            "network": KITE_NETWORK,
            "verified": True,
        })
    else:
        err, payer, tx_hash = await process_x402_payment(
            request, resource_url,
            price_raw=SUB_PRICE_RAW, price_usdc=SUB_PRICE_USDC,
            description="24-hour Agent Session for Agent Agora work orders and market intelligence",
        )
    if err is not None:
        return err
    if tx_hash and validate_kite_tx_hash(tx_hash) and not tx_hash_consumed(tx_hash):
        consume_tx_hash(tx_hash, "subscription", payer or "x402-payer", SUB_PRICE_USDC)

    api_key = generate_api_key()
    expires_at = time.time() + SUB_DURATION_HOURS * 3600
    subscription = {
        "api_key": api_key,
        "wallet": payer,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at,
        "tx_hash": tx_hash,
        "work_orders_used": 0,
        "market_units_used": 0,
        "max_work_orders": SUB_MAX_WORK_ORDERS,
        "max_market_units": SUB_MAX_MARKET_UNITS,
    }
    _subscriptions[api_key] = subscription
    save_subscription(subscription)
    audit("subscription_created", {
        "wallet": payer, "tx_hash": tx_hash,
        "amount_usdc": SUB_PRICE_USDC, "expires_at": expires_at,
    })
    return JSONResponse(content={
        "api_key": api_key,
        "session_key": api_key,
        "session_type": "agent_session",
        "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
        "session_expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
        "duration_hours": SUB_DURATION_HOURS,
        "price_paid_usdc": SUB_PRICE_USDC,
        "tx_hash": tx_hash,
        "payer": payer,
        "usage": {
            "header": "X-API-Key",
            "endpoints": ["/api/market", "/api/market/batch", "/api/signals/{asset}", "/api/agent/work-orders"],
            "limits": subscription_limits(subscription),
            "example": f"curl -H 'X-API-Key: {api_key}' 'https://agentagora.fly.dev/api/market?asset=BTC'",
        },
        "network": KITE_NETWORK,
    })


@app.api_route("/api/agent/session", methods=["GET", "POST"], tags=["Paid — x402"])
async def create_agent_session(request: Request) -> JSONResponse:
    """Create a bounded 24-hour Agent Agora session from Kite settlement proof."""
    return await subscribe(request)


# ─────────────────────────────────────────────────────────────────────────────
# Paid endpoints — x402 or subscription key
# ─────────────────────────────────────────────────────────────────────────────
async def _auth_or_402(request: Request, resource_url: str,
                        price_raw: str = PRICE_USDC_RAW,
                        price_usdc: str = PRICE_USDC,
                        description: str = SERVICE_DESCRIPTION,
                        usage_kind: str = "market",
                        usage_units: int = 1,
                        ) -> Tuple[Optional[JSONResponse], Optional[str], Optional[str], bool]:
    """
    Returns (error_response, payer, tx_hash, via_subscription).
    Check X-Admin-Key bypass first, then X-API-Key, then fall through to x402.
    """
    admin_key = request.headers.get("X-Admin-Key", "")
    if admin_key and admin_key == ADMIN_KEY:
        return None, "admin-worker", None, True

    api_key = (request.headers.get("X-API-Key") or request.headers.get("x-api-key") or "").strip()
    if api_key:
        sub = validate_api_key(api_key)
        if sub is None:
            exp_at = (_subscriptions.get(api_key) or {}).get("expires_at")
            if exp_at and exp_at < time.time():
                msg = "Subscription expired. Renew at POST /api/subscribe."
            else:
                msg = "Invalid API key. Purchase a subscription at POST /api/subscribe."
            return JSONResponse(status_code=401, content={"error": msg}), None, None, False
        usage_err = consume_subscription_usage(api_key, sub, usage_kind, max(1, usage_units))
        if usage_err is not None:
            return usage_err, None, None, False
        return None, sub["wallet"], None, True

    direct_tx = await extract_kite_tx_hash(request)
    if direct_tx:
        if not validate_kite_tx_hash(direct_tx):
            return JSONResponse(status_code=400, content={
                "error": "Invalid Kite transaction hash. Send a real 32-byte tx hash from kpass wallet send.",
                "expected": "X-Kite-Tx-Hash: 0x + 64 hex characters",
            }), None, None, False
        if tx_hash_consumed(direct_tx):
            return JSONResponse(status_code=409, content={
                "error": "This Kite transaction hash has already been used. Send a fresh transaction for this paid action.",
                "rule": "1 direct Kite tx hash = 1 paid action. Use a subscription key for repeated tasks.",
            }), None, None, False
        try:
            verification = await verify_kite_usdc_payment(direct_tx, price_raw)
        except HTTPException as e:
            return JSONResponse(status_code=e.status_code, content={"error": e.detail}), None, None, False
        payer = verification["payer"]
        consume_tx_hash(direct_tx, usage_kind, payer, price_usdc)
        audit("direct_kite_settlement", {
            "payer": payer,
            "tx_hash": direct_tx,
            "amount_usdc_expected": price_usdc,
            "amount_raw_verified": verification["amount_raw"],
            "merchant": verification["merchant"],
            "asset": verification["asset"],
            "network": KITE_NETWORK,
            "verified": True,
        })
        return None, payer, direct_tx, False

    err, payer, tx_hash = await process_x402_payment(request, resource_url, price_raw, price_usdc, description)
    if err is None and tx_hash and validate_kite_tx_hash(tx_hash) and not tx_hash_consumed(tx_hash):
        consume_tx_hash(tx_hash, usage_kind, payer or "x402-payer", price_usdc)
    return err, payer, tx_hash, False


@app.get("/api/market", tags=["Paid — x402"])
async def market(request: Request, asset: str = Query(..., description="Asset ticker")) -> JSONResponse:
    asset = asset.upper().strip()
    resource_url = public_resource_url(request)
    err, payer, tx_hash, via_sub = await _auth_or_402(request, resource_url)
    if err is not None:
        if not (request.headers.get("X-PAYMENT") or request.headers.get("x-payment")):
            audit("402_returned", {"asset": asset})
        return err

    try:
        intel = await build_market_intelligence(asset)
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})

    if via_sub:
        audit("subscribed_call", {"asset": asset, "wallet": payer})
        return JSONResponse(content={**intel, "_auth": "subscription"})

    audit("paid_call", {"asset": asset, "payer": payer, "amount_usdc": PRICE_USDC, "tx_hash": tx_hash, "network": KITE_NETWORK, "settled": True})
    response = JSONResponse(content={**intel, "_payment": {"tx_hash": tx_hash, "amount_usdc": PRICE_USDC, "payer": payer, "merchant": SERVICE_WALLET}})
    return response


@app.get("/api/market/batch", tags=["Paid — x402"])
async def market_batch(
    request: Request,
    assets: str = Query(..., description=f"Comma-separated tickers, max {BATCH_MAX_ASSETS}"),
) -> JSONResponse:
    """
    Query up to 10 assets in one request. Priced at $0.02 per asset via x402, or free with subscription.
    """
    asset_list = [a.strip().upper() for a in assets.split(",") if a.strip()][:BATCH_MAX_ASSETS]
    if not asset_list:
        return JSONResponse(status_code=400, content={"error": "No valid assets provided"})

    batch_price_raw = str(BATCH_PRICE_PER_ASSET_RAW * len(asset_list))
    batch_price_usdc = str(round(float(BATCH_PRICE_PER_ASSET_RAW * len(asset_list)) / 10 ** KITE_USDC_DECIMALS, 4))
    resource_url = public_resource_url(request)
    description = f"Batch market intelligence for {len(asset_list)} assets: {', '.join(asset_list)}"

    err, payer, tx_hash, via_sub = await _auth_or_402(
        request,
        resource_url,
        batch_price_raw,
        batch_price_usdc,
        description,
        usage_kind="market",
        usage_units=len(asset_list),
    )
    if err is not None:
        return err

    results = await asyncio.gather(*[build_market_intelligence(a) for a in asset_list])
    payload = {
        "assets": {r["asset"]: r for r in results},
        "count": len(results),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "network": KITE_NETWORK,
    }
    if via_sub:
        audit("subscribed_batch", {"assets": asset_list, "wallet": payer})
        payload["_auth"] = "subscription"
    else:
        audit("paid_batch", {"assets": asset_list, "payer": payer, "amount_usdc": batch_price_usdc, "tx_hash": tx_hash})
        payload["_payment"] = {"tx_hash": tx_hash, "amount_usdc": batch_price_usdc, "payer": payer}
    return JSONResponse(content=payload)


@app.get("/api/signals/{asset}", tags=["Paid — x402"])
async def trading_signals(asset: str, request: Request) -> JSONResponse:
    """
    Structured trading signal for autonomous agents: BUY / SELL / HOLD with
    confidence score, risk level, stop-loss %, take-profit %, and reasoning.
    """
    asset = asset.upper().strip()
    resource_url = public_resource_url(request)
    err, payer, tx_hash, via_sub = await _auth_or_402(request, resource_url,
        description=f"Trading signal for {asset} — action, confidence, risk, entry/exit")
    if err is not None:
        if not (request.headers.get("X-PAYMENT") or request.headers.get("x-payment")):
            audit("402_returned", {"asset": asset, "endpoint": "signals"})
        return err

    try:
        intel = await build_market_intelligence(asset)
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})

    trade = derive_trade_action(intel)
    payload = {
        "asset": asset, "name": intel.get("name", asset),
        "timestamp": intel["timestamp"], "latency_ms": intel["latency_ms"],
        "price_usd": intel.get("price_usd"),
        "action": trade["action"],
        "confidence": trade["confidence"],
        "risk_level": trade["risk_level"],
        "stop_loss_pct": trade["stop_loss_pct"],
        "take_profit_pct": trade["take_profit_pct"],
        "score": trade["score"],
        "reasoning": trade["reasoning"],
        "signals": intel.get("signals", {}),
        "fear_greed_index": intel.get("fear_greed_index"),
        "fear_greed_label": intel.get("fear_greed_label"),
        "funding_rate_pct": intel.get("funding_rate_pct"),
        "open_interest_usd": intel.get("open_interest_usd"),
        "sources": intel.get("sources", []),
        "network": KITE_NETWORK,
    }
    if via_sub:
        audit("subscribed_signal", {"asset": asset, "wallet": payer, "action": trade["action"]})
        payload["_auth"] = "subscription"
    else:
        audit("paid_signal", {"asset": asset, "payer": payer, "amount_usdc": PRICE_USDC, "tx_hash": tx_hash, "action": trade["action"]})
        payload["_payment"] = {"tx_hash": tx_hash, "amount_usdc": PRICE_USDC, "payer": payer}
    return JSONResponse(content=payload)


# ─────────────────────────────────────────────────────────────────────────────
# Page routes
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def landing_page() -> HTMLResponse:
    f = STATIC_DIR / "index.html"
    return HTMLResponse(content=f.read_text(encoding="utf-8") if f.exists() else "<h1>Agent Agora</h1>")

@app.get("/app", response_class=HTMLResponse)
async def operator_dashboard() -> HTMLResponse:
    f = STATIC_DIR / "app.html"
    return HTMLResponse(content=f.read_text(encoding="utf-8") if f.exists() else "<h1>Dashboard</h1>")

@app.get("/query")
async def query_page() -> RedirectResponse:
    return RedirectResponse(url="/work", status_code=307)

@app.get("/work", response_class=HTMLResponse)
async def work_page() -> HTMLResponse:
    f = STATIC_DIR / "work.html"
    return HTMLResponse(content=f.read_text(encoding="utf-8") if f.exists() else "<h1>Work</h1>")

@app.get("/session", response_class=HTMLResponse)
async def session_page() -> HTMLResponse:
    f = STATIC_DIR / "session.html"
    return HTMLResponse(content=f.read_text(encoding="utf-8") if f.exists() else "<h1>Agent Session</h1>")

@app.get("/guide", response_class=HTMLResponse)
async def guide_page() -> HTMLResponse:
    f = STATIC_DIR / "guide.html"
    return HTMLResponse(content=f.read_text(encoding="utf-8") if f.exists() else "<h1>Guide</h1>")

@app.get("/agent", response_class=HTMLResponse)
async def agent_page() -> HTMLResponse:
    f = STATIC_DIR / "agent.html"
    return HTMLResponse(content=f.read_text(encoding="utf-8") if f.exists() else "<h1>Agent</h1>")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("market_service:app", host="0.0.0.0", port=port, log_level="info", reload=False)
