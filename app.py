import time
from datetime import datetime, timezone, timedelta, date

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="New Token Listings", page_icon="🪙", layout="wide")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ListingsDashboard/1.0)",
    "Accept": "application/json",import html
from datetime import datetime, timezone, timedelta, date

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="New Token Listings", page_icon="🪙", layout="wide")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ListingsDashboard/1.0)",
    "Accept": "application/json",
}

# Display order + labels matching the requested layout.
EXCHANGE_DISPLAY = {
    "CoinSpot": "CoinSpot",
    "Swyftx": "Swyftx",
    "Coinbase": "Coinbase",
    "Kraken": "Kraken",
    "OKX": "OKX AU",
    "KuCoin": "Kucoin AU",
}
TYPE_ORDER = ["Convert", "Spot", "Perp"]
TYPE_COLOR = {"Convert": "#9c27b0", "Spot": "#4caf50", "Perp": "#e08a2e"}


def _get(url):
    return requests.get(url, headers=HEADERS, timeout=20).json()


# Each fetcher returns list of {exchange, token, quote, pair, category, list_ts}
def fetch_coinbase():
    data = _get("https://api.exchange.coinbase.com/products")
    out = []
    for p in data:
        if p.get("status") == "online" and p.get("base_currency") and p.get("quote_currency"):
            out.append(_row("Coinbase", p["base_currency"], p["quote_currency"], "Spot", None))
    return out


def fetch_kraken():
    data = _get("https://api.kraken.com/0/public/AssetPairs").get("result", {})
    out = []
    for p in data.values():
        ws = p.get("wsname") or ""
        if "/" in ws:
            base, quote = ws.split("/", 1)
            out.append(_row("Kraken", base, quote, "Spot", None))
    return out


def fetch_okx():
    out = []
    spot = _get("https://www.okx.com/api/v5/public/instruments?instType=SPOT").get("data", [])
    for x in spot:
        if x.get("state") == "live" and x.get("baseCcy") and x.get("quoteCcy"):
            ts = int(x["listTime"]) if x.get("listTime") else None
            out.append(_row("OKX", x["baseCcy"], x["quoteCcy"], "Spot", ts))
    swap = _get("https://www.okx.com/api/v5/public/instruments?instType=SWAP").get("data", [])
    for x in swap:
        if x.get("state") != "live":
            continue
        parts = (x.get("instId") or "").split("-")
        if len(parts) >= 2:
            ts = int(x["listTime"]) if x.get("listTime") else None
            out.append(_row("OKX", parts[0], parts[1], "Perp", ts))
    return out


def fetch_kucoin():
    out = []
    spot = _get("https://api.kucoin.com/api/v1/symbols").get("data", [])
    for x in spot:
        if x.get("enableTrading") and x.get("baseCurrency") and x.get("quoteCurrency"):
            out.append(_row("KuCoin", x["baseCurrency"], x["quoteCurrency"], "Spot", None))
    fut = _get("https://api-futures.kucoin.com/api/v1/contracts/active").get("data", [])
    for x in fut:
        if x.get("status") == "Open" and x.get("baseCurrency") and x.get("quoteCurrency"):
            base = "BTC" if x["baseCurrency"] == "XBT" else x["baseCurrency"]
            out.append(_row("KuCoin", base, x["quoteCurrency"], "Perp", None))
    return out


def fetch_coinspot():
    prices = _get("https://www.coinspot.com.au/pubapi/v2/latest").get("prices", {})
    return [_row("CoinSpot", s.upper(), "AUD", "Spot", None) for s in prices.keys()]


def fetch_swyftx():
    data = _get("https://api.swyftx.com.au/markets/assets/")
    if isinstance(data, dict):
        data = data.get("data", [])
    out = []
    for a in data:
        code = a.get("code")
        if a.get("tradable") and code and code != "AUD":
            out.append(_row("Swyftx", code, "AUD", "Spot", None))
    return out


def _row(exchange, token, quote, category, list_ts):
    token, quote = str(token).upper(), str(quote).upper()
    return {
        "exchange": exchange, "token": token, "quote": quote,
        "pair": f"{token}/{quote}", "category": category, "list_ts": list_ts,
    }


FETCHERS = {
    "Coinbase": fetch_coinbase, "Kraken": fetch_kraken, "OKX": fetch_okx,
    "KuCoin": fetch_kucoin, "CoinSpot": fetch_coinspot, "Swyftx": fetch_swyftx,
}


@st.cache_data(ttl=120, show_spinner=True)
def load_all():
    rows, errors = [], {}
    for name, fn in FETCHERS.items():
        try:
            rows.extend(fn())
        except Exception as e:
            errors[name] = str(e)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["exchange", "pair", "category"])
        df["listed_date"] = df["list_ts"].apply(
            lambda t: datetime.fromtimestamp(t / 1000, tz=timezone.utc).date()
            if pd.notna(t) and t else None
        )
    return df, errors


def record_snapshot(df):
    """Session-persistent: first time we see a pair, stamp today's date."""
    if "first_seen" not in st.session_state:
        st.session_state.first_seen = {}
    seen = st.session_state.first_seen
    today = date.today()
    for _, r in df.iterrows():
        key = (r["exchange"], r["pair"], r["category"])
        if key not in seen:
            seen[key] = today
    return seen


def new_in_window(df, seen, start_d, end_d):
    """Return {(exchange, type): [tokens]} newly listed within [start_d, end_d]."""
    result = {}
    for _, r in df.iterrows():
        ld = r["listed_date"]  # real date (OKX) if available
        if ld is None:
            ld = seen.get((r["exchange"], r["pair"], r["category"]))
        if ld and start_d <= ld <= end_d:
            result.setdefault((r["exchange"], r["category"]), []).append(r["pair"])
    return result


# ---------------- UI ----------------
st.title("🪙 Token Listings Dashboard")

df, errors = load_all()
if errors:
    st.warning("Some sources failed: " + ", ".join(f"{k}" for k in errors))
if df.empty:
    st.error("No data loaded.")
    st.stop()

seen = record_snapshot(df)

c1, c2, c3 = st.columns([1.3, 1.3, 1])
with c1:
    start_d = st.date_input("From", value=date.today() - timedelta(days=6))
with c2:
    end_d = st.date_input("To", value=date.today())
with c3:
    st.write("")
    st.write("")
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

if isinstance(start_d, tuple):
    start_d = start_d[0]

win = new_in_window(df, seen, start_d, end_d)

# Header bar
st.markdown(
    f"<h3 style='border-bottom:3px solid #111;padding-bottom:6px;'>Date: "
    f"{start_d.strftime('%B %d')} &ndash; {end_d.strftime('%B %d')}</h3>",
    unsafe_allow_html=True,
)

# Build grouped HTML table
rows_html = []
alt = False
for ex_key, ex_label in EXCHANGE_DISPLAY.items():
    # which types apply to this exchange
    if ex_key in ("OKX", "KuCoin"):
        types = ["Spot", "Perp"]
    elif ex_key in ("CoinSpot", "Swyftx"):
        types = ["Convert", "Spot"]
    else:  # Coinbase, Kraken
        types = ["Spot", "Perp"]
    for i, t in enumerate(types):
        alt = not alt
        bg = "#d9d9d9" if alt else "#ffffff"
        tokens = win.get((ex_key, t), [])
        tokens_txt = ", ".join(sorted(tokens)) if tokens else "-"
        ex_cell = f"<b>{html.escape(ex_label)}</b>" if i == 0 else ""
        color = TYPE_COLOR.get(t, "#333")
        rows_html.append(
            f"<tr style='background:{bg};'>"
            f"<td style='padding:8px 12px;width:180px;'>{ex_cell}</td>"
            f"<td style='padding:8px 12px;width:150px;color:{color};'>{t}</td>"
            f"<td style='padding:8px 12px;'>{html.escape(tokens_txt)}</td>"
            f"</tr>"
        )

table_html = (
    "<table style='border-collapse:collapse;width:100%;font-size:15px;'>"
    "<thead><tr style='background:#3b3b3b;color:#fff;'>"
    "<th style='padding:10px 12px;text-align:left;'>Exchange</th>"
    "<th style='padding:10px 12px;text-align:left;'>Type</th>"
    "<th style='padding:10px 12px;text-align:left;'>Token</th>"
    "</tr></thead><tbody>" + "".join(rows_html) + "</tbody></table>"
)
st.markdown(table_html, unsafe_allow_html=True)

st.caption(
    "New listings within the selected window. Real listing dates come from OKX; other "
    "exchanges are tracked from first-seen while the app is running (they populate over "
    "time and reset if the app restarts). 'Convert' has no public listed pairs, so it "
    "stays '-'. Perpetuals are available on OKX & KuCoin only."
)

with st.expander("Browse all pairs (full filterable table)"):
    st.dataframe(
        df.assign(listed=df["listed_date"].apply(lambda d: d.strftime("%Y-%m-%d") if d else ""))
        [["token", "pair", "exchange", "quote", "category", "listed"]]
        .rename(columns={"token": "Token", "pair": "Pair", "exchange": "Exchange",
                         "quote": "Quote", "category": "Category", "listed": "Listed"}),
        use_container_width=True, hide_index=True, height=400,
    )

}


def _get(url):
    return requests.get(url, headers=HEADERS, timeout=20).json()


# Each fetcher returns a list of pair dicts:
# {exchange, token, quote, pair, category, list_ts}
def fetch_coinbase():
    data = _get("https://api.exchange.coinbase.com/products")
    out = []
    for p in data:
        if p.get("status") != "online":
            continue
        base, quote = p.get("base_currency"), p.get("quote_currency")
        if base and quote:
            out.append(_row("Coinbase", base, quote, "Spot", None))
    return out


def fetch_kraken():
    data = _get("https://api.kraken.com/0/public/AssetPairs").get("result", {})
    out = []
    for p in data.values():
        ws = p.get("wsname") or ""
        if "/" in ws:
            base, quote = ws.split("/", 1)
            out.append(_row("Kraken", base, quote, "Spot", None))
    return out


def fetch_okx():
    out = []
    spot = _get("https://www.okx.com/api/v5/public/instruments?instType=SPOT").get("data", [])
    for x in spot:
        if x.get("state") == "live" and x.get("baseCcy") and x.get("quoteCcy"):
            ts = int(x["listTime"]) if x.get("listTime") else None
            out.append(_row("OKX", x["baseCcy"], x["quoteCcy"], "Spot", ts))
    swap = _get("https://www.okx.com/api/v5/public/instruments?instType=SWAP").get("data", [])
    for x in swap:
        if x.get("state") != "live":
            continue
        parts = (x.get("instId") or "").split("-")  # e.g. BTC-USD-SWAP
        if len(parts) >= 2:
            ts = int(x["listTime"]) if x.get("listTime") else None
            out.append(_row("OKX", parts[0], parts[1], "Perpetual", ts))
    return out


def fetch_kucoin():
    out = []
    spot = _get("https://api.kucoin.com/api/v1/symbols").get("data", [])
    for x in spot:
        if x.get("enableTrading") and x.get("baseCurrency") and x.get("quoteCurrency"):
            out.append(_row("KuCoin", x["baseCurrency"], x["quoteCurrency"], "Spot", None))
    fut = _get("https://api-futures.kucoin.com/api/v1/contracts/active").get("data", [])
    for x in fut:
        if x.get("status") == "Open" and x.get("baseCurrency") and x.get("quoteCurrency"):
            base = "BTC" if x["baseCurrency"] == "XBT" else x["baseCurrency"]
            out.append(_row("KuCoin", base, x["quoteCurrency"], "Perpetual", None))
    return out


def fetch_coinspot():
    prices = _get("https://www.coinspot.com.au/pubapi/v2/latest").get("prices", {})
    return [_row("CoinSpot", s.upper(), "AUD", "Spot", None) for s in prices.keys()]


def fetch_swyftx():
    data = _get("https://api.swyftx.com.au/markets/assets/")
    if isinstance(data, dict):
        data = data.get("data", [])
    out = []
    for a in data:
        code = a.get("code")
        if a.get("tradable") and code and code != "AUD":
            out.append(_row("Swyftx", code, "AUD", "Spot", None))
    return out


def _row(exchange, token, quote, category, list_ts):
    token, quote = str(token).upper(), str(quote).upper()
    return {
        "exchange": exchange,
        "token": token,
        "quote": quote,
        "pair": f"{token}/{quote}",
        "category": category,
        "list_ts": list_ts,
    }


FETCHERS = {
    "Coinbase": fetch_coinbase,
    "Kraken": fetch_kraken,
    "OKX": fetch_okx,
    "KuCoin": fetch_kucoin,
    "CoinSpot": fetch_coinspot,
    "Swyftx": fetch_swyftx,
}


@st.cache_data(ttl=120, show_spinner=True)
def load_all():
    rows, errors = [], {}
    for name, fn in FETCHERS.items():
        try:
            rows.extend(fn())
        except Exception as e:
            errors[name] = str(e)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["exchange", "pair", "category"])
        df["listed_date"] = df["list_ts"].apply(
            lambda t: datetime.fromtimestamp(t / 1000, tz=timezone.utc).date()
            if pd.notna(t) and t else None
        )
        df["listed"] = df["listed_date"].apply(lambda d: d.strftime("%Y-%m-%d") if d else "")
    return df, errors


# ---------------- UI ----------------
st.title("🪙 Token Listings Dashboard")
st.caption(
    "Trading pairs across six exchanges, with quote currency and category (Spot / "
    "Perpetual). OKX shows real listing dates; filter by exchange, category, quote, "
    "token or listing date."
)

top = st.columns([1, 5])
with top[0]:
    if st.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

df, errors = load_all()

if errors:
    st.warning("Some sources failed: " + ", ".join(f"{k} ({v[:60]})" for k, v in errors.items()))

if df.empty:
    st.error("No data loaded.")
    st.stop()

# Summary tiles
counts = df.groupby("exchange")["pair"].count().to_dict()
tiles = st.columns(len(FETCHERS))
for col, name in zip(tiles, FETCHERS):
    col.metric(name, counts.get(name, 0))

st.divider()

# Filters row 1
f1, f2, f3, f4 = st.columns([1.2, 1, 1, 1.5])
with f1:
    ex_sel = st.multiselect("Exchange", sorted(df["exchange"].unique()))
with f2:
    cat_sel = st.multiselect("Category", sorted(df["category"].unique()))
with f3:
    quote_sel = st.multiselect("Quote", sorted(df["quote"].unique()))
with f4:
    token_q = st.text_input("Search token", placeholder="e.g. BTC, TAO").strip().upper()

# Filters row 2 - date
dated = df["listed_date"].dropna()
min_d = dated.min() if not dated.empty else date(2020, 1, 1)
max_d = dated.max() if not dated.empty else date.today()

d1, d2, d3 = st.columns([1.2, 1.2, 2])
with d1:
    start_d = st.date_input("Listed from", value=None, min_value=min_d, max_value=max_d)
with d2:
    end_d = st.date_input("Listed to", value=None, min_value=min_d, max_value=max_d)
with d3:
    st.write("")
    st.write("")
    keep_undated = st.checkbox(
        "Include pairs without a listing date", value=True,
        help="Only OKX exposes real listing dates. Uncheck to show dated pairs only.",
    )

# Apply filters
view = df.copy()
if ex_sel:
    view = view[view["exchange"].isin(ex_sel)]
if cat_sel:
    view = view[view["category"].isin(cat_sel)]
if quote_sel:
    view = view[view["quote"].isin(quote_sel)]
if token_q:
    view = view[view["token"].str.contains(token_q, na=False)]

date_active = bool(start_d or end_d)
if date_active:
    has_date = view["listed_date"].notna()
    cond = has_date.copy()
    if start_d:
        cond &= view["listed_date"].apply(lambda d: d is not None and d >= start_d)
    if end_d:
        cond &= view["listed_date"].apply(lambda d: d is not None and d <= end_d)
    if keep_undated:
        cond |= ~has_date
    view = view[cond]
elif not keep_undated:
    view = view[view["listed_date"].notna()]

st.write(f"**{len(view):,} pairs** shown (of {len(df):,} total)")

view = view.sort_values(["listed", "exchange", "pair"], ascending=[False, True, True])
st.dataframe(
    view[["token", "pair", "exchange", "quote", "category", "listed"]].rename(
        columns={"token": "Token", "pair": "Pair", "exchange": "Exchange",
                 "quote": "Quote", "category": "Category", "listed": "Listed"}
    ),
    use_container_width=True,
    hide_index=True,
    height=560,
)

st.caption(
    f"Last updated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · cached 120s · "
    "AUD pairs come mainly from CoinSpot & Swyftx. Perpetuals from OKX & KuCoin futures. "
    "Only OKX exposes listing dates, so the date filter mainly affects OKX pairs. "
    "'Convert' is an instant-swap feature without public listed pairs, so it's not shown."
)
