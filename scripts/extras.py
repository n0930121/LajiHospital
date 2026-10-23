"""812拉基醫院：擴充資料源。

- 家屬探病：三大法人買賣超（證交所、櫃買中心）
- 檢驗科：月營收（證交所、櫃買中心 OpenAPI）
- 國際醫療中心：美股收盤（Yahoo Finance，失敗改用 Stooq）
- 疾管署監測站：總體經濟指標（Yahoo Finance，失敗改用 FRED）
- AI 輔助診斷：有設定 ANTHROPIC_API_KEY 才啟用
- 早晨交班報告與院內廣播（Discord / Telegram）

每個函式都「失敗就回傳 None」，由主程式決定沿用上一版。
"""
import csv
import io
import json
import os
import re
import datetime as dt
from urllib.parse import quote

import update as U  # 共用 fetch、num、note 等工具


# ---------------------------------------------------------------- 家屬探病：三大法人
def _find_cols(fields, *need):
    out = []
    for words in need:
        idx = next((i for i, f in enumerate(fields) if all(w in f for w in words)), None)
        out.append(idx)
    return out


def get_institutional(asof):
    """回傳 {代號: [外資, 投信, 自營商]}，單位：張（1000 股）。"""
    if not asof:
        return None
    res = {}
    ymd = asof.replace("-", "")
    try:
        js = json.loads(U.fetch(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={ymd}&selectType=ALLBUT0999&response=json", f"t86_{ymd}.json"))
        fields = js.get("fields") or (js.get("tables") or [{}])[0].get("fields") or []
        data = js.get("data") or (js.get("tables") or [{}])[0].get("data") or []
        ci, fi, ti, di, tot = _find_cols(fields, ("證券代號",), ("外", "買賣超"), ("投信", "買賣超"), ("自營商買賣超",), ("三大法人", "買賣超"))
        for r in data:
            code = str(r[ci]).strip()
            if len(code) != 4:
                continue
            f, t = U.num(r[fi]) or 0, U.num(r[ti]) or 0
            d = U.num(r[di]) if di is not None else (U.num(r[tot]) or 0) - f - t
            res[code] = [round(f / 1000), round(t / 1000), round((d or 0) / 1000)]
        print(f"法人：上市 {len(res)} 檔")
    except Exception as e:  # noqa: BLE001
        U.note(f"上市法人買賣超抓取失敗（{e}）")
    n0 = len(res)
    try:
        y, m, d = asof.split("-")
        roc = f"{int(y) - 1911}/{m}/{d}"
        js = json.loads(U.fetch(f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json&se=EW&t=D&d={quote(roc)}",
                                f"tpex_inst_{ymd}.json"))
        rows = js.get("aaData") or (js.get("tables") or [{}])[0].get("data") or []
        for r in rows:
            code = str(r[0]).strip()
            if len(code) != 4 or len(r) < 12:
                continue
            nums = [U.num(x) for x in r[2:]]
            total = nums[-1] or 0
            foreign = nums[8] if len(nums) > 8 and nums[8] is not None else 0   # 外資及陸資合計買賣超
            trust = nums[11] if len(nums) > 11 and nums[11] is not None else 0  # 投信買賣超
            res[code] = [round(foreign / 1000), round(trust / 1000), round((total - foreign - trust) / 1000)]
        print(f"法人：上櫃 {len(res) - n0} 檔")
    except Exception as e:  # noqa: BLE001
        U.note(f"上櫃法人買賣超抓取失敗（{e}）")
    return res or None


# ---------------------------------------------------------------- 檢驗科：月營收
def get_revenue():
    """回傳 {代號: [資料年月, 當月營收(億), 年增%, 月增%]}"""
    res = {}
    for url, key in [("https://openapi.twse.com.tw/v1/opendata/t187ap05_L", "rev_twse.json"),
                     ("https://www.tpex.org.tw/openapi/v1/mopsfe_t187ap05_O", "rev_tpex.json")]:
        try:
            for r in json.loads(U.fetch(url, key)):
                code = str(U.pick(r, "公司代號", "SecuritiesCompanyCode") or "").strip()
                rev = U.num(U.pick(r, "營業收入-當月營收", "當月營收"))
                if len(code) != 4 or rev is None:
                    continue
                res[code] = [str(U.pick(r, "資料年月") or ""), round(rev / 1e5, 2),
                             U.num(U.pick(r, "營業收入-去年同月增減(%)", "去年同月增減(%)")),
                             U.num(U.pick(r, "營業收入-上月比較增減(%)", "上月比較增減(%)"))]
        except Exception as e:  # noqa: BLE001
            U.note(f"月營收抓取失敗：{key}（{e}）")
    print(f"月營收 {len(res)} 家")
    return res or None


# ---------------------------------------------------------------- 美股與總經的價格序列
def yahoo_series(sym, key):
    js = json.loads(U.fetch(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(sym)}?range=6mo&interval=1d", key))
    r = js["chart"]["result"][0]
    ts, cl = r["timestamp"], r["indicators"]["quote"][0]["close"]
    out = {}
    for t, c in zip(ts, cl):
        if c is not None:
            out[dt.datetime.fromtimestamp(t, dt.timezone(dt.timedelta(hours=-5))).date().isoformat()] = round(c, 4)
    return out


def stooq_series(sym, key):
    s = {"^GSPC": "^spx", "^IXIC": "^ndq", "^SOX": "^sox"}.get(sym, sym.lower().replace("^", "") + ".us")
    text = U.fetch(f"https://stooq.com/q/d/l/?s={quote(s)}&i=d", key)
    out = {}
    for row in list(csv.DictReader(io.StringIO(text)))[-130:]:
        c = U.num(row.get("Close"))
        if c is not None:
            out[row["Date"]] = c
    return out


def fred_series(sid, key):
    text = U.fetch(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", key)
    out = {}
    for row in list(csv.reader(io.StringIO(text)))[1:][-400:]:
        if len(row) >= 2:
            v = U.num(row[1]) if row[1] != "." else None
            if v is not None:
                out[row[0]] = v
    return out


def get_us(lex):
    """回傳 {代號: {日期: 收盤}}"""
    out = {}
    for u in lex.get("us", []):
        t = u["t"]
        safe = re.sub(r"\W", "_", t)
        for fn, key in ((yahoo_series, f"us_{safe}.json"), (stooq_series, f"us_{safe}.csv")):
            try:
                s = fn(t, key)
                if len(s) >= 5:
                    out[t] = s
                    break
            except Exception:  # noqa: BLE001
                continue
        if t not in out:
            U.note(f"美股 {t} 抓取失敗")
    print(f"美股 {len(out)} 檔")
    return out or None


def get_macro(cfg):
    """回傳 {指標: {日期: 數值}}"""
    out = {}
    for m in cfg.get("macro", []):
        s = None
        if m.get("yahoo"):
            try:
                s = yahoo_series(m["yahoo"], f"macro_{m['k']}.json")
                if m.get("yscale"):
                    s = {d: round(v * m["yscale"], 3) if v > 20 else v for d, v in s.items()}  # 舊格式 ^TNX 為殖利率×10
            except Exception:  # noqa: BLE001
                s = None
        if not s and m.get("fred"):
            try:
                s = fred_series(m["fred"], f"fred_{m['k']}.csv")
            except Exception as e:  # noqa: BLE001
                U.note(f"總經 {m['name']} 抓取失敗（{e}）")
        if s:
            out[m["k"]] = s
    print(f"總經指標 {len(out)} 項")
    return out or None


def summarize_macro(cfg, series):
    """把每個指標整理成：最新值、日變動、一年內位置（0–1）、燈號。"""
    res = {}
    for m in cfg.get("macro", []):
        s = series.get(m["k"]) if series else None
        if not s:
            continue
        ds = sorted(s)
        vals = [s[d] for d in ds][-260:]
        last, prev = vals[-1], vals[-2] if len(vals) > 1 else vals[-1]
        rank = sum(v <= last for v in vals) / len(vals)
        if m["k"] == "vix":
            light = "red" if last >= 25 else "amber" if last >= 18 else "green"
        elif m["k"] == "curve":
            light = "red" if last < 0 else "amber" if last < 0.25 else "green"
        elif m["warn"] == "high":
            light = "red" if rank >= 0.85 else "amber" if rank >= 0.65 else "green"
        else:
            light = "amber" if rank >= 0.85 or rank <= 0.15 else "green"
        res[m["k"]] = {"organ": m["organ"], "name": m["name"], "unit": m["unit"], "note": m["note"], "date": ds[-1],
                       "v": round(last, 3), "chg": round(last - prev, 3), "rank": round(rank, 2), "light": light,
                       "spark": [round(v, 3) for v in vals[-60:]]}
    return res


# ---------------------------------------------------------------- 與網頁相同的比對規則（產生交班報告用）
class Matcher:
    def __init__(self, uni, lex):
        self.lex = lex
        amb = set(lex.get("ambiguous", []))
        names = []
        self.codes = {c for c, *_ in uni}
        for c, n, *_ in uni:
            base = re.sub(r"\*|-KY|-創|創$", "", n)
            for x in (n, base):
                if len(x) >= 2 and x not in amb:
                    names.append((x, c))
        names += [(k, v) for k, v in lex.get("alias", {}).items()]
        names += [(k, "G:" + k) for k in lex.get("groups", {})]
        seen, self.names = set(), []
        for n, c in sorted(names, key=lambda x: -len(x[0])):
            if n not in seen:
                seen.add(n)
                self.names.append((n, c))

    def match(self, text):
        codes, taken = [], [False] * len(text)
        for m in re.finditer(r"[（(](\d{4})[）)]", text):
            if m.group(1) in self.codes and m.group(1) not in codes:
                codes.append(m.group(1))
        for n, c in self.names:
            i = text.find(n)
            while i >= 0:
                if not any(taken[i:i + len(n)]):
                    for k in range(i, i + len(n)):
                        taken[k] = True
                    for x in (self.lex["groups"][c[2:]] if c.startswith("G:") else [c]):
                        if x not in codes:
                            codes.append(x)
                i = text.find(n, i + len(n))
        themes = [k for k, ws in self.lex["themes"].items() if any(w in text for w in ws)]
        return codes, themes


# ---------------------------------------------------------------- AI 輔助診斷
def ai_judge(news, uni_names, matcher, cfg):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return 0
    import requests
    a = cfg.get("ai", {})
    todo = [n for n in news if not n.get("ai") and not n.get("curated")][: a.get("max_per_run", 150)]
    done = 0
    for i in range(0, len(todo), a.get("batch", 30)):
        chunk = todo[i:i + a.get("batch", 30)]
        lines = []
        for k, n in enumerate(chunk):
            codes, _ = matcher.match(n["t"])
            lines.append(f"{k}. {n['t']}｜點名：{'、'.join(f'{c}{uni_names.get(c, "")}' for c in codes[:8]) or '無'}")
        prompt = ("你是台股新聞分析師。以下是新聞標題與標題中點名的股票。請逐則判斷：\n"
                  "sent：整體對台股是利多 1、中性 0、利空 -1；heat：市場關注度 1–5；"
                  "impact：對每檔點名股票個別的影響（1 利多、0 中性、-1 利空），只列點名的股票代號。\n"
                  "只輸出 JSON 陣列，例如 [{\"i\":0,\"sent\":1,\"heat\":3,\"impact\":{\"2330\":1}}]，不要其他文字。\n\n" + "\n".join(lines))
        try:
            hdr = {"Authorization": f"Bearer {key}", "anthropic-version": "2026-10-21", "content-type": "application/json"}
            if os.environ.get("ANTHROPIC_WORKSPACE_ID"):
                hdr["anthropic-workspace-id"] = os.environ["ANTHROPIC_WORKSPACE_ID"]
            r = requests.post("https://api.anthropic.com/v1/messages", timeout=90, headers=hdr,
                json={"model": a.get("model", "claude-haiku-5-5"), "max_tokens": 4000,
                      "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
            arr = json.loads(text[text.find("["): text.rfind("]") + 1])
            for x in arr:
                n = chunk[int(x["i"])]
                n["sent"] = max(-1, min(1, int(x.get("sent", n["sent"]))))
                n["heat"] = max(1, min(5, int(x.get("heat", n["heat"]))))
                imp = {c: max(-1, min(1, int(v))) for c, v in (x.get("impact") or {}).items() if re.fullmatch(r"\d{4}", str(c))}
                if imp:
                    n["impact"] = imp
                n["ai"] = True
                done += 1
        except Exception as e:  # noqa: BLE001
            detail = ""
            try:
                detail = "：" + r.text[:200]
            except Exception:  # noqa: BLE001
                pass
            U.note(f"AI 輔助診斷失敗，改用關鍵字規則（{e}{detail}）")
            break
    print(f"AI 輔助診斷 {done} 則")
    return done


# ---------------------------------------------------------------- 早晨交班報告
def handover(data, hist, lex, matcher):
    news, quotes, inst = data["news"], data["quotes"], data.get("inst") or {}
    uni = {r.split(",")[0]: r.split(",") for r in data["uni_raw"].split(";")}
    inds = data["inds"]
    name = lambda c: uni.get(c, [c, c])[1]  # noqa: E731
    dates = sorted({n["date"] for n in news if n.get("date")})
    if not dates:
        return None
    today = dates[-1]
    prior = dates[-6:-1]
    tn = [n for n in news if n.get("date") == today]
    items = []

    # 1. 門診量：題材聲量與爆量
    cnt, base = {}, {}
    for n in news:
        _, th = matcher.match(n["t"])
        for t in th:
            if n.get("date") == today:
                cnt[t] = cnt.get(t, 0) + 1
            elif n.get("date") in prior:
                base[t] = base.get(t, 0) + 1 / max(1, len(prior))
    hot = sorted(cnt.items(), key=lambda x: -x[1])[:3]
    surge = [t for t, c in cnt.items() if c >= 4 and c >= 2 * max(0.5, base.get(t, 0))]
    if hot:
        items.append({"k": "門診量", "t": "最熱的科別是" + "、".join(f"{t}科（{c} 則）" for t, c in hot) +
                      (f"；{'、'.join(surge)}科門診爆量，比前幾天多一倍以上。" if surge else "。")})

    # 2. 聯合會診：今天最常一起出現的兩個題材
    pair = {}
    for n in tn:
        _, th = matcher.match(n["t"])
        th = sorted(set(th) - {"總經風險", "法人籌碼"})
        for i in range(len(th)):
            for j in range(i + 1, len(th)):
                pair[(th[i], th[j])] = pair.get((th[i], th[j]), 0) + 1
    if pair:
        (a, b), c = max(pair.items(), key=lambda x: x[1])
        shared = {}
        for n in tn:
            codes, th = matcher.match(n["t"])
            if a in th and b in th:
                for x in codes:
                    shared[x] = shared.get(x, 0) + 1
        top = [name(x) for x, _ in sorted(shared.items(), key=lambda x: -x[1])[:3]]
        items.append({"k": "聯合會診", "t": f"{a}科與{b}科今天有 {c} 則新聞同時提到" + (f"，共同病人是{'、'.join(top)}" if top else "") + "。",
                      "pair": [a, b]})

    # 3. 國際疫情：美股
    us = data.get("us") or {}
    sox, nas = us.get("^SOX"), us.get("^IXIC")
    movers = sorted([(t, v) for t, v in us.items() if not t.startswith("^") and v.get("pct") is not None], key=lambda x: -abs(x[1]["pct"]))[:2]
    if sox or nas or movers:
        parts = []
        if sox:
            parts.append(f"費半{'上漲' if sox['pct'] >= 0 else '下跌'} {abs(sox['pct']):.1f}%")
        if nas:
            parts.append(f"那斯達克{'上漲' if nas['pct'] >= 0 else '下跌'} {abs(nas['pct']):.1f}%")
        for t, v in movers:
            ref = next((u for u in lex["us"] if u["t"] == t), None)
            tw = "、".join(name(c) for c in (ref or {}).get("tw", [])[:3])
            parts.append(f"{v['n']}{'漲' if v['pct'] >= 0 else '跌'} {abs(v['pct']):.1f}%，留意轉診病人{tw}")
        items.append({"k": "國際疫情", "t": "前一晚美股：" + "；".join(parts) + "。"})

    # 4. 疾管署：總經燈號
    mac = data.get("macro") or {}
    bad = [m for m in mac.values() if m["light"] in ("red", "amber")]
    if mac:
        if bad:
            def state(m):
                if m["organ"] == "全球體溫":
                    return "發燒" if m["light"] == "red" else "微燒"
                if m["organ"] == "呼吸":
                    return "倒掛，呼吸困難" if m["light"] == "red" else "曲線偏平"
                return ("亮紅燈，" if m["light"] == "red" else "") + ("處於一年高檔" if m["rank"] >= 0.5 else "處於一年低檔")
            items.append({"k": "疾管署", "t": "需要注意：" + "；".join(
                f"{m['organ']}（{m['name']} {m['v']}{m['unit']}）{state(m)}" for m in bad[:3]) + "。"})
        else:
            items.append({"k": "疾管署", "t": "總經各項指標都在正常範圍。"})

    # 5. 診斷不符：利多不漲、利空不跌
    net = {}
    for n in news:
        if n.get("date") not in dates[-2:]:
            continue
        codes, _ = matcher.match(n["t"])
        for c in codes:
            s = (n.get("impact") or {}).get(c, n["sent"])
            net[c] = net.get(c, 0) + s * n["heat"]
    up_bad = [c for c, v in net.items() if v >= 3 and c in quotes and quotes[c][1] < 0]
    dn_good = [c for c, v in net.items() if v <= -3 and c in quotes and quotes[c][1] > 0]
    if up_bad or dn_good:
        t = []
        if up_bad:
            t.append(f"利多不漲 {len(up_bad)} 檔（如{'、'.join(name(c) for c in up_bad[:3])}），留意利多出盡")
        if dn_good:
            t.append(f"利空不跌 {len(dn_good)} 檔（如{'、'.join(name(c) for c in dn_good[:3])}），可能是底部訊號")
        items.append({"k": "診斷不符", "t": "；".join(t) + "。"})

    # 6. 家屬探病：法人資金往哪幾個產業
    if inst:
        by = {}
        for c, (f, t, d) in inst.items():
            if c in uni:
                ind = inds[int(uni[c][3])]
                by[ind] = by.get(ind, 0) + f + t
        if by:
            s = sorted(by.items(), key=lambda x: -x[1])
            items.append({"k": "家屬探病", "t": f"外資加投信最多買進{s[0][0]}、{s[1][0]}；最多賣出{s[-1][0]}、{s[-2][0]}。"})

    # 7. 病危名單
    named = {}
    for n in tn:
        for c in matcher.match(n["t"])[0]:
            if c in quotes:
                named[c] = quotes[c]
    worst = sorted(named.items(), key=lambda x: x[1][1] / max(0.01, x[1][0] - x[1][1]))[:3]
    if worst and worst[0][1][1] < 0:
        items.append({"k": "病危名單", "t": "、".join(f"{name(c)}（{q[1] / (q[0] - q[1]) * 100:+.1f}%）" for c, q in worst if q[1] < 0) + "。"})
    return {"date": today, "items": items}


def handover_text(rep, site):
    if not rep:
        return ""
    lines = [f"🏥 812拉基醫院｜{rep['date']} 早晨交班報告"]
    lines += [f"【{x['k']}】{x['t']}" for x in rep["items"]]
    lines += ["", "以上為新聞與行情的觀察整理，不構成投資建議。", site]
    return "\n".join(lines)


def broadcast(text):
    import requests
    sent = []
    hook = os.environ.get("DISCORD_WEBHOOK_URL")
    if hook:
        try:
            requests.post(hook, json={"content": text[:1990]}, timeout=20).raise_for_status()
            sent.append("Discord")
        except Exception as e:  # noqa: BLE001
            U.note(f"Discord 廣播失敗（{e}）")
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": text}, timeout=20).raise_for_status()
            sent.append("Telegram")
        except Exception as e:  # noqa: BLE001
            U.note(f"Telegram 廣播失敗（{e}）")
    if sent:
        print("院內廣播已送出：" + "、".join(sent))
