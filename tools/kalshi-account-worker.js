// Cloudflare Worker: READ-ONLY live view of Alex's Kalshi account for the night-drive page.
// It signs requests with a READ-ONLY Kalshi API key (it can't place or cancel orders) and only
// answers GET /account: balance, open positions and the latest fills. Nothing else is forwarded.
//
// Setup (Cloudflare dashboard > Workers > Create > paste this file > Deploy), then
// Settings > Variables and Secrets > add two SECRETS:
//   KALSHI_KEY_ID       the key id Kalshi shows next to the read-only key
//   KALSHI_PRIVATE_KEY  the whole private key file text, BEGIN/END lines included
// Never put the key in this file or in the repo.

const KALSHI = "https://api.elections.kalshi.com/trade-api/v2";
const ORIGINS = ["https://alexanderhabel22-commits.github.io", "http://127.0.0.1:8765", "http://localhost:8765"];
const CACHE_MS = 1500;           // all open tabs share one Kalshi call per 1.5 s

let keyPromise = null, cached = null;

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const cors = {
      "Access-Control-Allow-Origin": ORIGINS.includes(origin) ? origin : ORIGINS[0],
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Vary": "Origin",
    };
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });
    if (request.method !== "GET") return new Response("read-only", { status: 405, headers: cors });
    if (new URL(request.url).pathname !== "/account") return new Response("not allowed", { status: 403, headers: cors });
    try {
      if (!cached || Date.now() - cached.t > CACHE_MS) cached = { t: Date.now(), body: JSON.stringify(await account(env)) };
      return new Response(cached.body, { headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" } });
    } catch (e) {
      return new Response(JSON.stringify({ error: String(e.message || e) }), { status: 502, headers: { ...cors, "Content-Type": "application/json" } });
    }
  },
};

async function account(env) {
  const since = Math.floor(Date.now() / 1000) - 3 * 86400;
  const [bal, pos, fills, sets] = await Promise.all([
    get(env, "/portfolio/balance"),
    get(env, "/portfolio/positions", "?limit=200&count_filter=position"),
    get(env, "/portfolio/fills", "?limit=25"),
    get(env, "/portfolio/settlements", `?limit=200&min_ts=${since}`).catch(() => ({ settlements: [] })),
  ]);
  const cash = money(bal, "balance"), positions = money(bal, "portfolio_value");
  return {
    at: new Date().toISOString(),
    cash, positions, total: cash != null && positions != null ? +(cash + positions).toFixed(2) : null,
    open: (pos.market_positions || []).map(p => ({
      ticker: p.ticker, position: num(p.position_fp ?? p.position),
      exposure: money(p, "market_exposure"), realized: money(p, "realized_pnl"),
    })),
    // settled markets of the last 3 days: what you paid, what came back (all $)
    settlements: (sets.settlements || []).map(x => ({
      ticker: x.ticker, result: x.market_result, at: x.settled_time,
      yes: num(x.yes_count_fp ?? x.yes_count), no: num(x.no_count_fp ?? x.no_count),
      cost: (money(x, "yes_total_cost") || 0) + (money(x, "no_total_cost") || 0),
      revenue: money(x, "revenue") || 0,
      fee: x.fee_cost == null ? 0 : (String(x.fee_cost).includes(".") ? +x.fee_cost : x.fee_cost / 100),
    })),
    fills: (fills.fills || []).map(f => ({
      id: f.trade_id || f.fill_id, ticker: f.ticker, side: f.side, action: f.action,
      count: num(f.count_fp ?? f.count),
      price: f.side === "no" ? num(f.no_price_dollars ?? (f.no_price != null ? f.no_price / 100 : null))
                             : num(f.yes_price_dollars ?? (f.yes_price != null ? f.yes_price / 100 : null)),
      at: f.created_time,
    })),
  };
}

// Kalshi sends money as integer cents (`key`) or a dollar string (`key_dollars`)
function money(d, key) {
  if (d[key] != null) return d[key] / 100;
  if (d[key + "_dollars"] != null) return +d[key + "_dollars"];
  return null;
}
const num = v => (v == null || v === "" ? null : +v);

async function get(env, path, query = "") {
  const ts = String(Date.now());
  const sig = await sign(env, ts + "GET" + "/trade-api/v2" + path);
  const r = await fetch(KALSHI + path + query, { headers: {
    "Accept": "application/json", "KALSHI-ACCESS-KEY": env.KALSHI_KEY_ID.trim(),
    "KALSHI-ACCESS-TIMESTAMP": ts, "KALSHI-ACCESS-SIGNATURE": sig } });
  if (!r.ok) throw new Error(`Kalshi ${r.status} on ${path}: ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

// RSA-PSS SHA-256, salt = digest length, the same signing the bot uses (wxbot/kalshi_api.py)
async function sign(env, msg) {
  keyPromise = keyPromise || importKey(env.KALSHI_PRIVATE_KEY);
  const s = await crypto.subtle.sign({ name: "RSA-PSS", saltLength: 32 }, await keyPromise, new TextEncoder().encode(msg));
  return btoa(String.fromCharCode(...new Uint8Array(s)));
}

export async function importKey(pem) {
  pem = pem.replace(/\\n/g, "\n");
  const pkcs1 = /BEGIN RSA PRIVATE KEY/.test(pem);
  const der = Uint8Array.from(atob(pem.replace(/-----[^-]+-----/g, "").replace(/\s+/g, "")), c => c.charCodeAt(0));
  return crypto.subtle.importKey("pkcs8", pkcs1 ? wrapPkcs1(der) : der, { name: "RSA-PSS", hash: "SHA-256" }, false, ["sign"]);
}

// Kalshi downloads keys as PKCS#1 ("BEGIN RSA PRIVATE KEY"); WebCrypto wants PKCS#8, so wrap it
function wrapPkcs1(k) {
  const len = n => n < 128 ? [n] : n < 256 ? [0x81, n] : n < 65536 ? [0x82, n >> 8, n & 255] : [0x83, n >> 16, (n >> 8) & 255, n & 255];
  const tlv = (tag, body) => [tag, ...len(body.length), ...body];
  const algo = [0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01, 0x05, 0x00];
  return new Uint8Array(tlv(0x30, [0x02, 0x01, 0x00, ...algo, ...tlv(0x04, [...k])]));
}
