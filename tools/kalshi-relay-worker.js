// Cloudflare Worker: read-only relay for Kalshi's PUBLIC market prices, so the
// dashboard in your browser can get live prices every 30 seconds.
// It only forwards GET requests for market data (no keys, no orders, no account).
// Deploys itself: .github/workflows/workers.yml pushes this file to the "kalshi-relay" worker
// whenever it changes on main (no more pasting into the Cloudflare editor).

const KALSHI = "https://api.elections.kalshi.com/trade-api/v2";
const ALLOWED = /^\/markets(\/[A-Za-z0-9._-]+)?$/;   // /markets and /markets/<ticker> only

export default {
  async fetch(request) {
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Access-Control-Allow-Headers": "*",
    };
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });
    if (request.method !== "GET") return new Response("read-only", { status: 405, headers: cors });

    const url = new URL(request.url);
    if (!ALLOWED.test(url.pathname)) return new Response("not allowed", { status: 403, headers: cors });

    const upstream = await fetch(KALSHI + url.pathname + url.search, {
      headers: { "Accept": "application/json" },
      cf: { cacheTtl: 10, cacheEverything: true },
    });
    const body = await upstream.text();
    return new Response(body, {
      status: upstream.status,
      headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "public, max-age=10" },
    });
  },
};
