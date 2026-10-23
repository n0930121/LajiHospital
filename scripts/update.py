#!/usr/bin/env python3
"""812拉基醫院：每日資料更新。

抓取：
  1. 上市、上櫃公司清單與產業別（證交所 ISIN 公告頁）
  2. 上市、上櫃每日收盤行情（證交所、櫃買中心 OpenAPI）
  3. 加權指數（證交所 OpenAPI）
  4. 台股新聞標題（RSS 與鉅亨網），以關鍵字規則標記利多／利空與熱度

輸出 docs/data.json。任何一個來源失敗時，沿用上一版的該項資料，不讓網站壞掉。

用法：
  python scripts/update.py                 # 正式抓取
  python scripts/update.py --offline DIR   # 用 DIR 內的樣本檔測試（不連網）
"""
import argparse
import datetime as dt
import email.utils
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data.json")
LEX = os.path.join(ROOT, "docs", "lexicon.json")
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
TPE = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime.now(TPE)

ISIN_TWSE = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
ISIN_TPEX = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"
TWSE_QUOTES = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TWSE_INDEX = "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
TPEX_QUOTES = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"

OFFLINE = None
NOTES = []


def note(msg):
    print("•", msg)
    NOTES.append(msg)


# ---------------------------------------------------------------- 連線
def fetch(url, key, binary=False):
    """抓網址；離線模式改讀 OFFLINE/<key>。"""
    if OFFLINE:
        path = os.path.join(OFFLINE, key)
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        data = open(path, "rb").read()
        return data if binary else data.decode("utf-8")
    import requests
    last = None
    for i in range(3):
        try:
            r = requests.get(url, timeout=40, headers={"User-Agent": "Mozilla/5.0 (812-laji-hospital updater)"})
            r.raise_for_status()
            return r.content if binary else r.text
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise last


def num(v):
    """把 '1,234.50'、'+3.5'、'-0.20'、'--' 轉成 float；無法解析回傳 None。"""
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def pick(d, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def roc_to_iso(s):
    s = re.sub(r"\D", "", str(s or ""))
    if len(s) == 7:
        return f"{int(s[:3]) + 1911}-{s[3:5]}-{s[5:7]}"
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return None


# ---------------------------------------------------------------- 公司清單
def parse_isin(page, market):
    rows, kind = [], ""
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S | re.I):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S | re.I)]
        if len(cells) == 1 or (len(cells) >= 1 and re.search(r"<b>", tr, flags=re.I) and len(cells) < 3):
            kind = html.unescape(cells[0]).strip()
            continue
        if len(cells) < 5:
            continue
        first = html.unescape(cells[0]).replace("　", " ").strip()
        m = re.match(r"^(\d{4})\s+(.+)$", first)
        if not m or kind not in ("股票", "創新板"):
            continue
        ind = html.unescape(cells[4]).strip() or "其他業"
        rows.append([m.group(1), m.group(2).strip(), market if kind == "股票" else "創新板", ind])
    return rows


def get_universe(prev):
    try:
        twse = parse_isin(fetch(ISIN_TWSE, "isin_twse.html", binary=True).decode("cp950", "ignore"), "上市")
        tpex = parse_isin(fetch(ISIN_TPEX, "isin_tpex.html", binary=True).decode("cp950", "ignore"), "上櫃")
        uni = twse + tpex
        if len(uni) < 500:
            raise ValueError(f"公司數只有 {len(uni)} 家，疑似格式改變")
        print(f"公司清單：上市 {len(twse)}、上櫃 {len(tpex)}")
        return uni
    except Exception as e:  # noqa: BLE001
        note(f"公司清單抓取失敗，沿用上一版（{e}）")
        if not prev:
            return []
        mks, inds = prev["mks"], prev["inds"]
        return [[c, n, mks[int(m)], inds[int(i)]] for c, n, m, i in (r.split(",") for r in prev["uni_raw"].split(";"))]


# ---------------------------------------------------------------- 行情
def get_quotes(prev):
    quotes, asof = {}, None
    try:
        rows = json.loads(fetch(TWSE_QUOTES, "twse_quotes.json"))
        for r in rows:
            code = pick(r, "Code", "證券代號")
            close, chg = num(pick(r, "ClosingPrice", "收盤價")), num(pick(r, "Change", "漲跌價差"))
            val = num(pick(r, "TradeValue", "成交金額"))
            if code and len(code) == 4 and close is not None:
                quotes[code] = [close, chg or 0.0, round((val or 0) / 1e8, 2)]
            asof = asof or roc_to_iso(pick(r, "Date", "日期"))
        print(f"上市行情 {len(quotes)} 檔，日期 {asof}")
    except Exception as e:  # noqa: BLE001
        note(f"上市行情抓取失敗（{e}）")
    n0 = len(quotes)
    try:
        rows = json.loads(fetch(TPEX_QUOTES, "tpex_quotes.json"))
        for r in rows:
            code = pick(r, "SecuritiesCompanyCode", "Code", "代號")
            close = num(pick(r, "Close", "ClosingPrice", "收盤"))
            chg = num(pick(r, "Change", "漲跌"))
            val = num(pick(r, "TransactionAmount", "TradeValue", "成交金額(元)", "成交金額"))
            if code and len(code) == 4 and close is not None:
                quotes[code] = [close, chg or 0.0, round((val or 0) / 1e8, 2)]
            asof = asof or roc_to_iso(pick(r, "Date", "日期"))
        print(f"上櫃行情 {len(quotes) - n0} 檔")
    except Exception as e:  # noqa: BLE001
        note(f"上櫃行情抓取失敗（{e}）")
    if not quotes:
        note("今天沒有拿到任何行情，沿用上一版")
        return (prev or {}).get("quotes", {}), (prev or {}).get("asof")
    return quotes, asof


def get_taiex(prev):
    try:
        rows = json.loads(fetch(TWSE_INDEX, "twse_index.json"))
        for r in rows:
            if not any("發行量加權股價指數" == str(v).strip() for v in r.values()):
                continue
            close = num(pick(r, "收盤指數", "ClosingIndex", "CloseIndex"))
            pts = num(pick(r, "漲跌點數", "ChangePoints", "ChangePoint"))
            sign = str(pick(r, "漲跌", "Change", "Dir") or "")
            pct = num(pick(r, "漲跌百分比", "ChangePercent", "ChangePercentage"))
            if pts is not None and "-" in sign and pts > 0:
                pts = -pts
            if pct is not None and pts is not None and pts < 0 and pct > 0:
                pct = -pct
            if close:
                return {"close": close, "chg": pts or 0.0, "pct": pct if pct is not None else round((pts or 0) / (close - (pts or 0)) * 100, 2)}
        raise ValueError("找不到發行量加權股價指數")
    except Exception as e:  # noqa: BLE001
        note(f"加權指數抓取失敗，沿用上一版（{e}）")
        return (prev or {}).get("taiex")


# ---------------------------------------------------------------- 新聞
def clean_title(t):
    t = html.unescape(re.sub(r"<[^>]+>", "", t or "")).strip()
    t = re.sub(r"\s+", " ", t)
    return t


def parse_rss(xml_text, default_src):
    out = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        title = clean_title(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        src_el = it.find("source")
        src = (src_el.text.strip() if src_el is not None and src_el.text else default_src)
        if src_el is not None and title.endswith(" - " + src):
            title = title[: -len(src) - 3].strip()
        when = None
        pd = it.findtext("pubDate")
        if pd:
            try:
                when = email.utils.parsedate_to_datetime(pd).astimezone(TPE)
            except Exception:  # noqa: BLE001
                when = None
        out.append({"t": title, "u": link, "src": src, "when": when})
    return out


def get_news(keywords):
    items = []
    cfg = CFG["news"]
    for k, feed in enumerate(cfg.get("rss", [])):
        urls = [feed["url"]] if "{q}" not in feed["url"] else [feed["url"].replace("{q}", quote(q)) for q in feed.get("queries", [])]
        for j, url in enumerate(urls):
            try:
                got = parse_rss(fetch(url, f"rss_{k}_{j}.xml"), feed["name"])
                items += got
            except Exception as e:  # noqa: BLE001
                note(f"新聞來源 {feed['name']} 第 {j + 1} 個查詢失敗（{e}）")
    cn = cfg.get("cnyes")
    if cn:
        try:
            data = json.loads(fetch(cn["url"], "cnyes.json"))
            lst = (((data or {}).get("items") or {}).get("data")) or []
            for x in lst:
                ts = x.get("publishAt")
                items.append({"t": clean_title(x.get("title")), "src": cn["name"],
                              "u": f"https://news.cnyes.com/news/id/{x.get('newsId')}" if x.get("newsId") else "",
                              "when": dt.datetime.fromtimestamp(ts, TPE) if ts else None})
        except Exception as e:  # noqa: BLE001
            note(f"新聞來源 鉅亨網 失敗（{e}）")
    # 只留跟台股題材或上市櫃公司有關的標題
    kept = [x for x in items if x["t"] and any(w in x["t"] for w in keywords)]
    print(f"新聞：抓到 {len(items)} 則，與台股相關 {len(kept)} 則")
    return kept


def norm(t):
    return re.sub(r"[\s\W_]+", "", t)


def similar(a, b):
    if a in b or b in a:
        return True
    ga = {a[i:i + 2] for i in range(len(a) - 1)}
    gb = {b[i:i + 2] for i in range(len(b) - 1)}
    return bool(ga and gb) and len(ga & gb) / len(ga | gb) > 0.55


def score(title, cover):
    s = CFG["sentiment"]
    pos = sum(w for k, w in s["positive"].items() if k in title)
    neg = sum(w for k, w in s["negative"].items() if k in title)
    sent = 1 if pos - neg >= 1 else -1 if neg - pos >= 1 else 0
    heat = 2 + min(2, cover - 1) + (1 if any(k in title for k in s["strong"]) else 0)
    return sent, max(1, min(5, heat))


def merge_news(fresh, prev_news):
    keep_from = (NOW - dt.timedelta(days=CFG["news"]["keep_days"])).date()
    groups = []  # 每組是同一則新聞的不同來源
    # 先放上一版留下來的新聞（保留歷史，讓時間軸有累積）
    for p in prev_news or []:
        groups.append({"t": p["t"], "u": p.get("u", ""), "src": p.get("src", ""), "d": p.get("d"), "s": p.get("s", ""),
                       "cover": p.get("cover", 1), "date": p.get("date"), "old": True})
    for x in fresh:
        when = x["when"] or NOW
        if when.date() < keep_from:
            continue
        n = norm(x["t"])
        g = next((g for g in groups if similar(norm(g["t"]), n)), None)
        if g:
            if not g.get("old"):
                g["cover"] += 1
            continue
        groups.append({"t": x["t"], "u": x["u"], "src": x["src"], "d": f"{when.month}/{when.day}", "s": "",
                       "cover": 1, "date": when.strftime("%Y-%m-%d")})
    out = []
    for g in groups:
        if g.get("date") and g["date"] < keep_from.isoformat():
            continue
        sent, heat = score(g["t"], g["cover"])
        old = next((p for p in prev_news or [] if p["t"] == g["t"]), {}) if g.get("old") else {}
        if old.get("curated"):  # 人工整理的新聞保留原判讀；其餘每天用最新規則重算
            sent, heat = old.get("sent", sent), old.get("heat", heat)
        item = {"d": g["d"], "date": g.get("date"), "t": g["t"], "s": g["s"], "src": g["src"], "u": g["u"],
                "sent": sent, "heat": heat, "cover": g["cover"]}
        if old.get("curated"):
            item["curated"] = True
        out.append(item)
    out.sort(key=lambda x: (x.get("date") or "", x["heat"]), reverse=True)
    return out[: CFG["news"]["max_items"]]


# ---------------------------------------------------------------- 歷史行情
HIST = os.path.join(ROOT, "docs", "history.json")
TWSE_DAY = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d}&type=ALLBUT0999&response=json"


def parse_mi_index(js):
    """證交所 MI_INDEX（某一天全部上市股票）→ {代號: 收盤價}"""
    tables = js.get("tables")
    if tables is None:  # 舊格式：fields9 / data9
        tables = [{"fields": js[k], "data": js.get("data" + k[6:], [])} for k in js if k.startswith("fields")]
    for t in tables or []:
        f = t.get("fields") or []
        if "證券代號" in f and "收盤價" in f:
            ci, pi = f.index("證券代號"), f.index("收盤價")
            out = {}
            for row in t.get("data") or []:
                code, close = str(row[ci]).strip(), num(row[pi])
                if len(code) == 4 and close is not None:
                    out[code] = close
            return out
    return {}


def update_history(quotes, asof, keep=40, target=23):
    try:
        h = json.load(open(HIST, encoding="utf-8"))
        rows = {d: {} for d in h["dates"]}
        for code, arr in h["close"].items():
            for d, v in zip(h["dates"], arr):
                if v is not None:
                    rows[d][code] = v
    except Exception:  # noqa: BLE001
        rows = {}
    if asof and quotes:
        rows[asof] = {c: q[0] for c, q in quotes.items() if len(c) == 4}
    # 歷史不足一個月時，用證交所單日行情往回補（只補上市股）
    if asof and len(rows) < target:
        day = dt.date.fromisoformat(asof)
        tries = 0
        while len(rows) < target and tries < 45:
            day -= dt.timedelta(days=1)
            tries += 1
            iso = day.isoformat()
            if day.weekday() >= 5 or iso in rows:
                continue
            try:
                got = parse_mi_index(json.loads(fetch(TWSE_DAY.format(d=day.strftime("%Y%m%d")), f"mi_{day:%Y%m%d}.json")))
                if got:
                    rows[iso] = got
                if not OFFLINE:
                    time.sleep(2.5)  # 證交所限制查詢頻率
            except Exception as e:  # noqa: BLE001
                if not isinstance(e, FileNotFoundError):
                    note(f"回補 {iso} 行情失敗（{e}）")
    dates = sorted(rows)[-keep:]
    codes = sorted({c for d in dates for c in rows[d]})
    hist = {"dates": dates, "close": {c: [rows[d].get(c) for d in dates] for c in codes}}
    with open(HIST, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, separators=(",", ":"))
    print(f"歷史行情：{len(dates)} 個交易日、{len(codes)} 檔")


# ---------------------------------------------------------------- 主流程
def main():
    global OFFLINE
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", help="以樣本資料夾取代網路抓取")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    OFFLINE = a.offline
    prev = json.load(open(a.out, encoding="utf-8")) if os.path.exists(a.out) else None
    lex = json.load(open(LEX, encoding="utf-8"))

    uni = get_universe(prev)
    quotes, asof = get_quotes(prev)
    taiex = get_taiex(prev)
    try:
        update_history(quotes, asof)
    except Exception as e:  # noqa: BLE001
        note(f"歷史行情更新失敗（{e}）")

    words = set()
    for ws in lex["themes"].values():
        words.update(ws)
    words.update(lex["groups"].keys())
    words.update(lex["alias"].keys())
    amb = set(lex.get("ambiguous", []))
    for _, name, _, _ in uni:
        base = re.sub(r"\*|-KY|-創|創$", "", name)
        for n in (name, base):
            if len(n) >= 2 and n not in amb:
                words.add(n)
    fresh = get_news(words)
    news = merge_news(fresh, (prev or {}).get("news"))
    if not news:
        note("沒有任何新聞，沿用上一版")
        news = (prev or {}).get("news", [])

    inds = sorted({r[3] for r in uni})
    mks = ["上市", "上櫃", "創新板"]
    data = {
        "asof": asof,
        "updated": NOW.strftime("%Y-%m-%d %H:%M"),
        "taiex": taiex,
        "inds": inds,
        "mks": mks,
        "uni_raw": ";".join(f"{c},{n},{mks.index(m)},{inds.index(i)}" for c, n, m, i in uni),
        "quotes": quotes,
        "news": news,
        "notes": NOTES,
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"完成：{len(uni)} 家公司、{len(quotes)} 檔行情、{len(news)} 則新聞，行情日 {asof}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
