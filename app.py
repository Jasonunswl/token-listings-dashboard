import html
import json
import base64
from datetime import datetime, timezone, timedelta, date

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="New Token Listings", page_icon="\u{1FA99}", layout="wide")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ListingsDashboard/1.0)",
    "Accept": "application/json",
}

EXCHANGE_DISPLAY = {
    "CoinSpot": "CoinSpot",
    "Swyftx": "Swyftx",
    "Coinbase": "Coinbase",
    "Kraken": "Kraken",
    "OKX": "OKX AU",
    "KuCoin": "Kucoin AU",
}
TYPE_COLOR = {"Convert": "#9c27b0", "Spot": "#4caf50", "Perp": "#e08a2e"}
BASELINE_DATE = date(2000, 1, 1)

# ---- Persistent storage config (GitHub-backed) ----
# Set these in Streamlit secrets to enable durable, cross-restart new-listing
# detection for all exchanges. If GITHUB_TOKEN is absent the app silently
# falls back to session-only tracking (baseline resets on restart).
SNAPSHOT_REPO = "Jasonunswl/token-listings-dashboard"
SNAPSHOT_PATH = "snapshot.json"
SNAPSHOT_BRANCH = "main"


def _gh_token():
    try:
        return st.secrets.get("GITHUB_TOKEN")
    except Exception:
        return None


def _gh_headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ListingsDashboard",
    }


def load_persistent_snapshot():
    """Return (seen_dict, sha) from snapshot.json in the repo, or ({}, None)."""
    token = _gh_token()
    if not token:
        return None, None
    url = f"https://api.github.com/repos/{SNAPSHOT_REPO}/contents/{SNAPSHOT_PATH}?ref={SNAPSHOT_BRANCH}"
    try:
        r = requests.get(url, headers=_gh_headers(token), timeout=20)
        if r.status_code == 404:
            return {}, None
        r.raise_for_status()
        payload = r.json()
        raw = base64.b64decode(payload["content"]).decode("utf-8")
        data = json.loads(raw) if raw.strip() else {}
        seen = {}
        for k, v in data.items():
            parts = k.split("|")
            if len(parts) == 3:
                seen[(parts[0], parts[1], parts[2])] = _as_date(v)
        return seen, payload.get("sha")
    except Exception:
        return None, None


def save_persistent_snapshot(seen, sha):
    token = _gh_token()
    if not token:
        return False
    data = {}
    for (ex, pair, cat), d in seen.items():
        if d is not None:
            data[f"{ex}|{pair}|{cat}"] = d.strftime("%Y-%m-%d")
    body = base64.b64encode(json.dumps(data, indent=0, sort_keys=True).encode("utf-8")).decode("ascii")
    url = f"https://api.github.com/repos/{SNAPSHOT_REPO}/contents/{SNAPSHOT_PATH}"
    payload = {
        "message": f"Update listing snapshot {date.today().isoformat()}",
        "content": body,
        "branch": SNAPSHOT_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    try:
        r = requests.put(url, headers=_gh_headers(token), json=payload, timeout=20)
        r.raise_for_status()
        return True
    except Exception:
        return False


def _get(url):
    return requests.get(url, headers=HEADERS, timeout=20).json()


def _as_date(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        ts = pd.Timestamp(v)
        if pd.isna(ts):
            return None
        return ts.date()
    except Exception:
        return None


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
            lambda t: _as_date(datetime.fromtimestamp(t / 1000, tz=timezone.utc))
            if pd.notna(t) and t else None
        )
    return df, errors


def record_snapshot(df):
    """Persistent-first tracking with session fallback.

    Returns (seen, persistent) where persistent indicates durable storage
    is active. When persistent, new pairs are stamped with today's date and
    written back to the repo so detection survives app restarts.
    """
    persistent_seen, sha = load_persistent_snapshot()

    if persistent_seen is not None:
        # Durable mode via GitHub snapshot.json
        seen = dict(persistent_seen)
        first_run = len(seen) == 0
        stamp = BASELINE_DATE if first_run else date.today()
        changed = False
        for _, r in df.iterrows():
            key = (r["exchange"], r["pair"], r["category"])
            if key not in seen:
                seen[key] = stamp
                changed = True
        if changed:
            save_persistent_snapshot(seen, sha)
        return seen, True

    # Session-only fallback (resets on restart)
    first_run = "first_seen" not in st.session_state
    if first_run:
        st.session_state.first_seen = {}
    seen = st.session_state.first_seen
    stamp = BASELINE_DATE if first_run else date.today()
    for _, r in df.iterrows():
        key = (r["exchange"], r["pair"], r["category"])
        if key not in seen:
            seen[key] = stamp
    return seen, False


def new_in_window(df, seen, start_d, end_d):
    result = {}
    for _, r in df.iterrows():
        ld = _as_date(r["listed_date"])
        if ld is None:
            ld = _as_date(seen.get((r["exchange"], r["pair"], r["category"])))
        if ld is not None and ld != BASELINE_DATE and start_d <= ld <= end_d:
            result.setdefault((r["exchange"], r["category"]), []).append(r["pair"])
    return result


def build_dashboard():
    st.title("\u{1FA99} Token Listings Dashboard")

    df, errors = load_all()
    if errors:
        st.warning("Some sources failed: " + ", ".join(f"{k}" for k in errors))
    if df.empty:
        st.error("No data loaded.")
        st.stop()

    seen, persistent = record_snapshot(df)

    c1, c2, c3 = st.columns([1.3, 1.3, 1])
    with c1:
        start_d = st.date_input("From", value=date.today() - timedelta(days=6), key="from_d")
    with c2:
        end_d = st.date_input("To", value=date.today(), key="to_d")
    with c3:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.button("\u{1F504} Refresh", key="refresh_btn"):
            st.cache_data.clear()
            st.rerun()

    start_d = _as_date(start_d[0] if isinstance(start_d, tuple) else start_d)
    end_d = _as_date(end_d[0] if isinstance(end_d, tuple) else end_d)

    win = new_in_window(df, seen, start_d, end_d)

    st.markdown(
        f"<h3 style='border-bottom:3px solid #111;padding-bottom:6px;'>Date: "
        f"{start_d.strftime('%B %d')} &ndash; {end_d.strftime('%B %d')}</h3>",
        unsafe_allow_html=True,
    )

    rows_html = []
    alt = False
    for ex_key, ex_label in EXCHANGE_DISPLAY.items():
        types = ["Convert", "Spot"] if ex_key in ("CoinSpot", "Swyftx") else ["Spot", "Perp"]
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

    if persistent:
        st.caption(
            "\u2705 Persistent tracking active. New listings are recorded durably "
            "and survive app restarts. OKX shows real listing dates immediately; "
            "the other exchanges are flagged as new the first date a genuinely new "
            "pair appears after the baseline. 'Convert' has no public listed pairs. "
            "Perpetuals are available on OKX & KuCoin only."
        )
    else:
        st.caption(
            "New listings within the selected window. OKX shows real listing dates "
            "immediately; the other exchanges show '-' until genuinely new pairs "
            "appear after first load (baseline set on first run, resets if the app "
            "restarts \u2014 add a GITHUB_TOKEN secret to enable persistent tracking). "
            "'Convert' has no public listed pairs. Perpetuals are available on OKX & KuCoin only."
        )

    with st.expander("Browse all pairs (full filterable table)"):
        st.dataframe(
            df.assign(listed=df["listed_date"].apply(lambda d: d.strftime("%Y-%m-%d") if d else ""))
            [["token", "pair", "exchange", "quote", "category", "listed"]]
            .rename(columns={"token": "Token", "pair": "Pair", "exchange": "Exchange",
                             "quote": "Quote", "category": "Category", "listed": "Listed"}),
            use_container_width=True, hide_index=True, height=400,
        )


build_dashboard()
