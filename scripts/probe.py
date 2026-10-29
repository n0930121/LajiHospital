"""檢查各行情來源今天的資料是否已上架、欄位長什麼樣（診斷用）。"""
import datetime as dt
import json
import requests

H = {"User-Agent": "Mozilla/5.0 (812-laji-hospital probe)"}
now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
d = now.date()
roc = f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"
out = {"now": now.isoformat()}


def get(name, url):
    try:
        r = requests.get(url, headers=H, timeout=40)
        out[name] = {"status": r.status_code, "len": len(r.content)}
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            out[name]["head"] = r.text[:300]
    except Exception as e:  # noqa: BLE001
        out[name] = {"error": str(e)[:200]}
    return None


js = get("twse_mi", f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d:%Y%m%d}&type=ALLBUT0999&response=json")
if js:
    out["twse_mi"].update({"stat": js.get("stat"), "date": js.get("date"), "keys": list(js.keys())[:12]})
    out["twse_mi"]["tables"] = [{"title": t.get("title"), "fields": t.get("fields"), "rows": (t.get("data") or [])[:2]} for t in (js.get("tables") or [])][:10]
for name, url in [("tpex_wn1430", f"https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php?l=zh-tw&d={roc}&se=EW&o=json"),
                  ("tpex_www", f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={d:%Y/%m/%d}&type=EW&response=json")]:
    js = get(name, url)
    if js:
        out[name]["keys"] = list(js.keys())[:15]
        out[name]["date"] = js.get("date") or js.get("reportDate")
        if js.get("tables"):
            out[name]["tables"] = [{"title": t.get("title"), "date": t.get("date"), "fields": t.get("fields"), "rows": (t.get("data") or [])[:2], "n": len(t.get("data") or [])} for t in js["tables"]][:4]
        if js.get("aaData"):
            out[name]["aaData"] = js["aaData"][:2]
            out[name]["n"] = len(js["aaData"])
for name, url in [("twse_openapi", "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"),
                  ("tpex_openapi", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes")]:
    js = get(name, url)
    if isinstance(js, list) and js:
        out[name]["first"] = js[0]
json.dump(out, open("probe.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False)[:3000])
