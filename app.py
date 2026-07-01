import time
from datetime import datetime, timezone, timedelta

import requests
import streamlit as st

st.set_page_config(page_title="New Token Listings", page_icon="🪙", layout="wide")

# Exchanges tracked across the dashboard.
EXCHANGES = ["Coinbase", "Kraken", "OKX (Global)", "KuCoin", "CoinSpot (AU)", "Swyftx (AU)"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ListingsDashboard/1.0)",
    "Accept": "application/json",
}
RECENT_DAYS = 30  # window for surfacing recent listings where real dates exist


def _get(url):
    return requests.get(url, headers=HEADERS, timeout=15).json()


def fetch_coinbase():
    data = _get("https://api.exchange.coinbase.com/products")
    return [{"symbol": p["base_currency"], "list_ts": None}
            for p in data if p.get("status") == "online"]


def fetch_kraken():
    data = _get("https://api.kraken.com/0/public/AssetPairs").get("result", {})
    out = []
    for p in data.values():
        base = (p.get("base") or "").lstrip("XZ") or p.get("base")
        if base:
            out.append({"symbol": base, "list_ts": None})
    return out


def fetch_okx():
    data = _get("https://www.okx.com/api/v5/public/instruments?instType=SPOT").get("data", [])
    out = []
    for x in data:
        if x.get("state") == "live":
            ts = int(x["listTime"]) if x.get("listTime") else None
            out.append({"symbol": x.get("baseCcy") or x.get("instId"), "list_ts": ts})
    return out


def fetch_kucoin():
    data = _get("https://api.kucoin.com/api/v1/symbols").get("data", [])
    return [{"symbol": x.get("baseCurrency") or x["symbol"], "list_ts": None}
            for x in data if x.get("enableTrading")]


def fetch_coinspot():
    prices = _get("https://www.coinspot.com.au/pubapi/v2/latest").get("prices", {})
    return [{"symbol": s.upper(), "list_ts": None} for s in prices.keys()]


def fetch_swyftx():
    data = _get("https://api.swyftx.com.au/markets/assets/")
    if isinstance(data, dict):
        data = data.get("data", [])
    return [{"symbol": a.get("code"), "list_ts": None}
            for a in data if a.get("tradable") and a.get("code")]


FETCHERS = {
    "Coinbase": fetch_coinbase,
    "Kraken": fetch_kraken,
    "OKX (Global)": fetch_okx,
    "KuCoin": fetch_kucoin,
    "CoinSpot (AU)": fetch_coinspot,
    "Swyftx (AU)": fetch_swyftx,
}


@st.cache_data(ttl=120, show_spinner=False)
def load_exchange(name):
    try:
        items = FETCHERS[name]()
        merged = {}
        for it in items:
            sym = str(it["symbol"]).upper()
            ts = it["list_ts"]
            if sym not in merged or (ts and (merged[sym] is None or ts < merged[sym])):
                merged[sym] = ts
        return merged, None
    except Exception as e:
        return {}, str(e)


def recent_listings(symbols_map):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).timestamp() * 1000
    recent = [(s, ts) for s, ts in symbols_map.items() if ts and ts >= cutoff]
    recent.sort(key=lambda x: x[1], reverse=True)
    return recent


st.title("🪙 New Token Listings Dashboard")
st.caption(
    "Tracks tradable assets across six exchanges. Exchanges that expose real listing "
    "dates (OKX) show genuinely recent listings; others show total tracked assets and "
    "flag changes across your session refreshes."
)

if st.button("🔄 Refresh"):
    st.cache_data.clear()
    st.rerun()

if "snapshots" not in st.session_state:
    st.session_state.snapshots = {}

st.write("")
cols = st.columns(3)

for i, name in enumerate(EXCHANGES):
    symbols_map, err = load_exchange(name)
    prev = st.session_state.snapshots.get(name)

    new_since_last = []
    if prev is not None:
        new_since_last = sorted(set(symbols_map) - set(prev))
    st.session_state.snapshots[name] = set(symbols_map)

    recent = recent_listings(symbols_map)

    with cols[i % 3]:
        with st.container(border=True):
            st.subheader(name)
            if err:
                st.error(f"Fetch failed: {err}")
                continue
            st.metric("Assets tracked", len(symbols_map))

            if new_since_last:
                st.success(f"🆕 {len(new_since_last)} new since last refresh")
                st.write(", ".join(new_since_last[:40]))
            elif recent:
                st.write(f"**Recently listed (last {RECENT_DAYS}d):**")
                for sym, ts in recent[:15]:
                    d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                    st.write(f"• **{sym}** — {d}")
            else:
                st.caption("No new listings detected yet. Baseline saved; "
                           "new coins appear on future refreshes.")

st.divider()
st.caption(f"Last updated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · "
           "Data cached 120s. CoinSpot's public API exposes a limited set of coins.")
