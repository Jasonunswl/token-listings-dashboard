import html
import json
import base64
import re
from datetime import datetime, timezone, timedelta, date

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="New Token Listings", page_icon="💰", layout="wide")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ListingsDashboard/1.0)",
    "Accept": "application/json",
}
HTML_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-AU,en;q=0.9",
}

EXCHANGE_DISPLAY = {
    "CoinSpot": "CoinSpot",
    "Swyftx": "Swyftx",
    "Coinbase": "Coinbase",
    "Kraken": "Kraken",
    "OKX": "OKX AU",
    "KuCoin": "KuCoin AU",
}
TYPE_COLOR = {"Convert": "#9c27b0", "Spot": "#4caf50", "Perp": "#e08a2e"}
BASELINE_DATE = date(2000, 1, 1)
# Coinbase backfilled a floor timestamp on legacy assets; ignore it as "not a real listing date".
COINBASE_FLOOR = "2023-01-01"
ANN_PAGES = 4

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


def _to_ms(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n < 1_000_000_000_000:
        n *= 1000
    return n


def _date_to_ms(d):
    return int(d.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _iso_to_ms(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(d.timestamp() * 1000)
    except Exception:
        return None


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


def fetch_coinbase():
    """Coinbase Advanced Trade market products carry real listing dates via the
    'new_at' field (and a 'new' flag). Legacy assets share a backfilled floor
    timestamp, which we treat as undated."""
    out = []
    try:
        data = _get("https://api.coinbase.com/api/v3/brokerage/market/products?limit=1000")
        products = data.get("products", []) if isinstance(data, dict) else []
    except Exception:
        products = []
    for p in products:
        base = p.get("base_currency_id") or p.get("base_display_symbol")
        quote = p.get("quote_currency_id") or p.get("quote_display_symbol")
        if not base or not quote:
            continue
        ptype = (p.get("product_type") or "").upper()
        cat = "Perp" if ("FUTURE" in ptype or "PERP" in ptype) else "Spot"
        new_at = p.get("new_at") or ""
        ms = None if (not new_at or new_at.startswith(COINBASE_FLOOR)) else _iso_to_ms(new_at)
        out.append(_row("Coinbase", base, quote, cat, ms))
    # Fallback to the Exchange catalogue if the Advanced Trade endpoint is unavailable.
    if not out:
        try:
            data = _get("https://api.exchange.coinbase.com/products")
            for p in data:
                if p.get("status") == "online" and p.get("base_currency") and p.get("quote_currency"):
                    out.append(_row("Coinbase", p["base_currency"], p["quote_currency"], "Spot", None))
        except Exception:
            pass
    return out


def _parse_written_date(txt):
    txt = re.sub(r"(\d{1,2})(st|nd|rd|th)", r"\1", txt).strip().rstrip(",")
    try:
        return datetime.strptime(txt.replace(",", ""), "%B %d %Y")
    except ValueError:
        return None


def fetch_kraken():
    """Kraken listings with real dates from the Asset Listings announcements,
    merged with the full trading catalogue for the browse-all table."""
    out = []
    seen_pairs = set()
    try:
        resp = requests.get(
            "https://blog.kraken.com/category/product/asset-listings",
            headers=HTML_HEADERS, timeout=20,
        )
        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = html.unescape(text)
        pat = re.compile(
            r"([A-Z0-9]{2,10})\s+is\s+available\s+for\s+trading.*?"
            r"((?:January|February|March|April|May|June|July|August|September|"
            r"October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})",
            re.I | re.S,
        )
        for tok, dtxt in pat.findall(text):
            tok = tok.upper()
            if tok in seen_pairs:
                continue
            d = _parse_written_date(dtxt)
            ms = _date_to_ms(d) if d else None
            out.append(_row("Kraken", tok, "USD", "Spot", ms))
            seen_pairs.add(tok)
    except Exception:
        pass
    try:
        data = _get("https://api.kraken.com/0/public/AssetPairs").get("result", {})
        for p in data.values():
            ws = p.get("wsname") or ""
            if "/" in ws:
                base, quote = ws.split("/", 1)
                if base.upper() in seen_pairs:
                    continue
                out.append(_row("Kraken", base, quote, "Spot", None))
    except Exception:
        pass
    return out


def _okx_parse_title(title):
    t = title
    is_perp = bool(re.search(r"perp|x-perp|perpetual|futures", t, re.I))
    m = re.search(r"([A-Z0-9]{2,15})\s*/\s*([A-Z0-9]{2,6})", t)
    if m:
        quote = m.group(2)
        if quote.startswith("USD") and quote not in ("USDT", "USDC"):
            quote = "USD"
        return m.group(1), quote, ("Perp" if is_perp else "Spot")
    m = re.search(r"\b([A-Z0-9]{2,12}?)(USDT|USDC|USD|EUR|BTC|ETH)\b", t)
    if m:
        return m.group(1), m.group(2), ("Perp" if is_perp else "Spot")
    m = re.search(r"for\s+([A-Z0-9]{2,12})\s+crypto", t)
    if m:
        return m.group(1), "USDT", ("Perp" if is_perp else "Spot")
    return None, None, None


def fetch_okx():
    out = []
    for page in range(1, ANN_PAGES + 1):
        url = f"https://www.okx.com/en-au/help/section/announcements-new-listings?page={page}"
        try:
            resp = requests.get(url, headers=HTML_HEADERS, timeout=20)
            body = resp.text
        except Exception:
            break
        text = re.sub(r"<[^>]+>", " ", body)
        pat = re.compile(
            r"(OKX (?:to|will)[^<]{5,120}?)\s*Published on\s+(\d{1,2}\s+\w+\s+\d{4})",
            re.I,
        )
        for title, dtxt in pat.findall(text):
            if re.search(r"delist", title, re.I):
                continue
            base, quote, cat = _okx_parse_title(html.unescape(title))
            if not base:
                continue
            try:
                d = datetime.strptime(dtxt.strip(), "%d %B %Y")
                ms = _date_to_ms(d)
            except ValueError:
                ms = None
            out.append(_row("OKX", base, quote, cat, ms))
    return out


def fetch_kucoin():
    # KuCoin does not operate a separate Australian site (kucoin.com.au is unavailable).
    # Per the requirement to track AU listings only, we do NOT pull KuCoin's global feed.
    # KuCoin AU will show "-" until an AU-specific source becomes available.
    return []


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


def _prefer_usd(df):
    """For each (exchange, token, category) group, if a USD-quoted pair exists,
    keep only the USD one and drop USDC/USDT (and other) duplicates. If no USD
    pair exists, keep whatever pairs are present."""
    if df.empty:
        return df
    df = df.copy()
    df["_is_usd"] = df["quote"] == "USD"
    keep = []
    grouped = df.groupby(["exchange", "token", "category"], sort=False)
    for _, g in grouped:
        if g["_is_usd"].any():
            keep.append(g[g["_is_usd"]])
        else:
            keep.append(g)
    result = pd.concat(keep, ignore_index=True)
    return result.drop(columns=["_is_usd"])


@st.cache_data(ttl=300, show_spinner=True)
def load_all():
    rows, errors = [], {}
    for name, fn in FETCHERS.items():
        try:
            rows.extend(fn())
        except Exception as e:
            errors[name] = str(e)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["listed_date"] = df["list_ts"].apply(
            lambda t: _as_date(datetime.fromtimestamp(t / 1000, tz=timezone.utc))
            if pd.notna(t) and t else None
        )
        df = df.sort_values("listed_date", na_position="last")
        df = df.drop_duplicates(subset=["exchange", "pair", "category"], keep="first")
        # Prefer XXX/USD over XXX/USDC and XXX/USDT for the same token.
        df = _prefer_usd(df)
    return df, errors


def record_snapshot(df):
    persistent_seen, sha = load_persistent_snapshot()
    if persistent_seen is not None:
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
    st.title("💰 Token Listings Dashboard")

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
        if st.button("Refresh", key="refresh_btn"):
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
            tokens_txt = ", ".join(sorted(set(tokens))) if tokens else "-"
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

    st.info(
        "Sources with real listing dates: OKX AU (okx.com/en-au announcements), "
        "Kraken (blog.kraken.com Asset Listings), and Coinbase (Advanced Trade "
        "'new_at' field). KuCoin has no separate Australian site, so KuCoin AU "
        "shows '-'. CoinSpot and Swyftx are Australian exchanges (AUD) with no "
        "published listing dates, so their new pairs are detected by day-over-day snapshot."
    )

    st.caption(
        "Pairs are de-duplicated per token: when a token lists in USD as well as "
        "USDC/USDT, only the USD pair is shown."
    )

    if persistent:
        st.caption(
            "Persistent tracking active. OKX AU, Kraken and Coinbase show real listing "
            "dates from their APIs/announcements; CoinSpot and Swyftx do not publish "
            "listing dates, so a pair is flagged the first date it appears after the "
            "baseline. 'Convert' has no public listed pairs."
        )
    else:
        st.caption(
            "New listings within the selected window. OKX AU, Kraken and Coinbase show "
            "real listing dates; CoinSpot and Swyftx show '-' until genuinely new pairs "
            "appear after first load (resets on restart - add a GITHUB_TOKEN secret for "
            "persistent tracking). 'Convert' has no public listed pairs."
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
