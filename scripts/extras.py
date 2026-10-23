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
def _rev_rows_json(rows):
    out = {}
    for r in rows:
        code = str(U.pick(r, "公司代號", "SecuritiesCompanyCode") or "").strip()
        rev = U.num(U.pick(r, "營業收入-當月營收", "當月營收"))
        if len(code) != 4 or rev is None:
            continue
        out[code] = [str(U.pick(r, "資料年月") or ""), round(rev / 1e5, 2),
                     U.num(U.pick(r, "營業收入-去年同月增減(%)", "去年同月增減(%)")),
                     U.num(U.pick(r, "營業收入-上月比較增減(%)", "上月比較增減(%)"))]
    return out


def _rev_rows_mops(raw, ym):
    """公開資訊觀測站的月營收彙總表（HTML）。欄位：代號、名稱、當月、上月、去年當月、月增%、年增%……，單位千元。"""
    for enc in ("utf-8", "cp950", "big5"):
        try:
            page = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S | re.I):
        cells = [re.sub(r"<[^>]+>|&nbsp;", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S | re.I)]
        if len(cells) < 7 or not re.fullmatch(r"\d{4}", cells[0]):
            continue
        rev = U.num(cells[2])
        if rev is None:
            continue
        out[cells[0]] = [ym, round(rev / 1e5, 2), U.num(cells[6]), U.num(cells[5])]
    return out


def get_revenue():
    """回傳 {代號: [資料年月, 當月營收(億), 年增%, 月增%]}。上櫃有三個備用來源，依序嘗試。"""
    res = {}
    try:
        res.update(_rev_rows_json(json.loads(U.fetch("https://openapi.twse.com.tw/v1/opendata/t187ap05_L", "rev_twse.json", tries=1, timeout=20))))
    except Exception as e:  # noqa: BLE001
        U.note(f"上市月營收抓取失敗（{e}）")
    n0 = len(res)
    got, errs = {}, []
    for url, key in [("https://www.tpex.org.tw/openapi/v1/mopsfe_t187ap05_O", "rev_tpex.json"),
                     ("https://www.tpex.org.tw/openapi/v1/t187ap05_O", "rev_tpex2.json")]:
        try:
            got = _rev_rows_json(json.loads(U.fetch(url, key, tries=1, timeout=20)))
            if len(got) > 100:
                break
        except Exception as e:  # noqa: BLE001
            errs.append(f"{key}：{str(e)[:60]}")
    if len(got) <= 100:  # 改抓公開資訊觀測站：最近一個月的營收約在次月 10 日前公布，依序往前找
        today = U.NOW.date()
        for back in (1, 2):
            y, m = today.year, today.month - back
            while m <= 0:
                y, m = y - 1, m + 12
            roc = y - 1911
            for host in ("https://mopsov.twse.com.tw", "https://mops.twse.com.tw"):
                key = f"rev_mops_{roc}_{m}.html"
                try:
                    got = _rev_rows_mops(U.fetch(f"{host}/nas/t21/otc/t21sc03_{roc}_{m}_0.html", key, binary=True, tries=1, timeout=25), f"{roc}{m:02d}")
                    if len(got) > 100:
                        break
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{key}：{str(e)[:60]}")
            if len(got) > 100:
                break
    if len(got) > 100:
        for c, v in got.items():
            res.setdefault(c, v)
    else:
        U.note("上櫃月營收所有來源都失敗（" + "；".join(errs[-4:]) + "）")
    print(f"月營收：上市 {n0} 家、上櫃 {len(res) - n0} 家")
    return res or None


# ---------------------------------------------------------------- 美股與總經的價格序列
def yahoo_series(sym, key):
    js = json.loads(U.fetch(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(sym)}?range=6mo&interval=1d", key, tries=1, timeout=12))
    r = js["chart"]["result"][0]
    ts, cl = r["timestamp"], r["indicators"]["quote"][0]["close"]
    out = {}
    for t, c in zip(ts, cl):
        if c is not None:
            out[dt.datetime.fromtimestamp(t, dt.timezone(dt.timedelta(hours=-5))).date().isoformat()] = round(c, 4)
    return out


def stooq_series(sym, key):
    s = {"^GSPC": "^spx", "^IXIC": "^ndq", "^SOX": "^sox"}.get(sym, sym.lower().replace("^", "") + ".us")
    text = U.fetch(f"https://stooq.com/q/d/l/?s={quote(s)}&i=d", key, tries=1, timeout=12)
    out = {}
    for row in list(csv.DictReader(io.StringIO(text)))[-130:]:
        c = U.num(row.get("Close"))
        if c is not None:
            out[row["Date"]] = c
    return out


def fred_series(sid, key):
    text = U.fetch(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", key, tries=1, timeout=15)
    out = {}
    for row in list(csv.reader(io.StringIO(text)))[1:][-400:]:
        if len(row) >= 2:
            v = U.num(row[1]) if row[1] != "." else None
            if v is not None:
                out[row[0]] = v
    return out


def _one_us(t):
    safe = re.sub(r"\W", "_", t)
    for fn, key in ((yahoo_series, f"us_{safe}.json"), (stooq_series, f"us_{safe}.csv")):
        try:
            s = fn(t, key)
            if len(s) >= 5:
                return s
        except Exception:  # noqa: BLE001
            continue
    return None


def get_us(lex):
    """回傳 {代號: {日期: 收盤}}；多檔同時抓，縮短等待時間。"""
    from concurrent.futures import ThreadPoolExecutor
    out = {}
    ts = [u["t"] for u in lex.get("us", [])]
    with ThreadPoolExecutor(max_workers=6) as ex:
        for t, s in zip(ts, ex.map(_one_us, ts)):
            if s:
                out[t] = s
            else:
                U.note(f"美股 {t} 抓取失敗")
    print(f"美股 {len(out)} 檔")
    return out or None


def get_macro(cfg):
    """回傳 {指標: {日期: 數值}}"""
    out = {}
    for m in cfg.get("macro", []):
        if m.get("diff"):  # 由其他兩個指標相減得出
            a, b = (out.get(k) for k in m["diff"])
            if a and b:
                out[m["k"]] = {d: round(a[d] - b[d], 3) for d in a if d in b}
            else:
                U.note(f"總經 {m['name']} 無法計算（缺少來源指標）")
            continue
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
        self.glob = set(lex.get("global_themes", []))   # 全院科室：大盤消息，不算產業科別
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
    glob = {t: c for t, c in cnt.items() if t in matcher.glob}
    cnt = {t: c for t, c in cnt.items() if t not in matcher.glob}
    hot = sorted(cnt.items(), key=lambda x: -x[1])[:3]
    surge = [t for t, c in cnt.items() if c >= 4 and c >= 2 * max(0.5, base.get(t, 0))]
    if hot:
        gtxt = "；全院科室：" + "、".join(f"{t} {c} 則" for t, c in sorted(glob.items(), key=lambda x: -x[1])) if glob else ""
        items.append({"k": "門診量", "t": "最熱的產業科別是" + "、".join(f"{t}科（{c} 則）" for t, c in hot) +
                      (f"；{'、'.join(surge)}科門診爆量，比前幾天多一倍以上" if surge else "") + gtxt + "。"})

    # 2. 聯合會診：今天最常一起出現的兩個題材
    pair = {}
    for n in tn:
        _, th = matcher.match(n["t"])
        th = sorted(set(th) - matcher.glob)
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


# ================================================================ 第三期：法人歷史、行事曆、Python 版診斷、週報、主治病人
def t86(ymd):
    """單日上市三大法人：{代號: 外資+投信 張數}"""
    js = json.loads(U.fetch(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={ymd}&selectType=ALLBUT0999&response=json", f"t86_{ymd}.json", tries=1, timeout=20))
    fields = js.get("fields") or (js.get("tables") or [{}])[0].get("fields") or []
    data = js.get("data") or (js.get("tables") or [{}])[0].get("data") or []
    if not fields or not data:
        return {}
    ci, fi, ti = _find_cols(fields, ("證券代號",), ("外", "買賣超"), ("投信", "買賣超"))
    out = {}
    for r in data:
        code = str(r[ci]).strip()
        if len(code) == 4:
            out[code] = round(((U.num(r[fi]) or 0) + (U.num(r[ti]) or 0)) / 1000)
    return out


def update_inst_history(inst, asof, keep=20, target=10):
    """把每天的外資＋投信買賣超存進 history.json，用來算「家屬連續探病天數」。不足 10 天時往回補上市股。"""
    import time
    try:
        h = json.load(open(U.HIST, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    rows = {d: {} for d in h.get("inst_dates", [])}
    for c, arr in (h.get("inst") or {}).items():
        for d, v in zip(h.get("inst_dates", []), arr):
            if v is not None:
                rows[d][c] = v
    if inst and asof:
        rows[asof] = {c: v[0] + v[1] for c, v in inst.items()}
    if asof and len(rows) < target:
        day, tries = dt.date.fromisoformat(asof), 0
        while len(rows) < target and tries < 20:
            day -= dt.timedelta(days=1)
            tries += 1
            if day.weekday() >= 5 or day.isoformat() in rows:
                continue
            try:
                got = t86(day.strftime("%Y%m%d"))
                if got:
                    rows[day.isoformat()] = got
                if not U.OFFLINE:
                    time.sleep(2.5)
            except Exception:  # noqa: BLE001
                continue
    dates = sorted(rows)[-keep:]
    codes = sorted({c for d in dates for c in rows[d]})
    h["inst_dates"] = dates
    h["inst"] = {c: [rows[d].get(c) for d in dates] for c in codes}
    with open(U.HIST, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, separators=(",", ":"))
    print(f"法人歷史：{len(dates)} 天")


def build_calendar(cfg, news, matcher, today):
    """門診預約表：固定的總經事件，加上從新聞標題擷取的法說會、財報日期。"""
    ev = [dict(e) for e in cfg.get("calendar", []) if e["date"] >= today]
    y = int(today[:4])
    seen = set()
    for n in news:
        t = n["t"]
        if not any(k in t for k in ("法說", "財報", "股東會", "除息", "除權")):
            continue
        m = re.search(r"(\d{1,2})[/月](\d{1,2})日?", t)
        if not m:
            continue
        mo, da = int(m.group(1)), int(m.group(2))
        if not (1 <= mo <= 12 and 1 <= da <= 31):
            continue
        yy = y + 1 if mo < int(today[5:7]) - 2 else y
        try:
            d = dt.date(yy, mo, da).isoformat()
        except ValueError:
            continue
        if d < today:
            continue
        codes, _ = matcher.match(t)
        kind = "法說會" if "法說" in t else "財報" if "財報" in t else "股東會" if "股東會" in t else "除權息"
        key = (d, tuple(codes[:2]), kind)
        if key in seen:
            continue
        seen.add(key)
        ev.append({"date": d, "t": t, "kind": "news", "tag": kind, "codes": codes[:3], "u": n.get("u", "")})
    ev.sort(key=lambda e: (e["date"], e["kind"] != "macro"))
    return ev[:40]


STAGE_TXT = {
    "recover": ("康復中", "消息與股價同向，趨勢延續中，持續追蹤即可"),
    "nopush": ("利多不漲", "好消息但股價不買單，留意利多出盡"),
    "resist": ("利空不跌", "壞消息股價卻撐住，可能已提前反映"),
    "critical": ("病危", "利空與賣壓同時出現，先隔離觀察"),
    "chips": ("籌碼先行", "還沒有明顯消息，但法人已在進場"),
    "leave": ("家屬撤離", "消息面平靜但法人在賣，暫緩加碼"),
    "observe": ("觀察中", "訊號還不明確，維持觀察"),
}


def news_index(news, matcher, days=3):
    """近 N 個新聞日：{代號: [新聞...]}"""
    ds = sorted({n["date"] for n in news if n.get("date")})[-days:]
    idx = {}
    for n in news:
        if n.get("date") not in ds:
            continue
        for c in matcher.match(n["t"])[0]:
            idx.setdefault(c, []).append(n)
    return idx


def diagnose_py(code, data, idx):
    """與網頁相同的綜合診斷規則（Telegram 與推播用）。"""
    q = (data.get("quotes") or {}).get(code)
    pct = q[1] / (q[0] - q[1]) * 100 if q and q[0] != q[1] else 0
    ns = idx.get(code, [])
    net = sum((n.get("impact") or {}).get(code, n["sent"]) * n["heat"] for n in ns)
    iv = (data.get("inst") or {}).get(code)
    fam = iv[0] + iv[1] if iv else 0
    pos, neg, up, dn = net >= 3, net <= -3, pct > .3, pct < -.3
    st = "observe"
    if pos and up: st = "recover"
    elif pos and dn: st = "nopush"
    elif neg and up: st = "resist"
    elif neg and dn: st = "critical"
    elif not pos and not neg and fam >= 300: st = "chips"
    elif not pos and not neg and fam <= -300: st = "leave"
    return {"st": st, "label": STAGE_TXT[st][0], "rx": STAGE_TXT[st][1], "pct": pct, "q": q, "net": net, "n": len(ns), "inst": iv, "news": ns}


def watch_list():
    try:
        return json.load(open(os.path.join(U.ROOT, "docs", "watch.json"), encoding="utf-8")).get("codes", [])
    except Exception:  # noqa: BLE001
        return []


def watch_text(data, matcher):
    codes = watch_list()
    if not codes:
        return ""
    uni = {r.split(",")[0]: r.split(",")[1] for r in data["uni_raw"].split(";")}
    idx = news_index(data["news"], matcher)
    lines, alarm = [], []
    for c in codes:
        d = diagnose_py(c, data, idx)
        mark = "🚨" if d["st"] in ("critical", "nopush", "leave") else "🟢" if d["st"] in ("recover", "resist", "chips") else "⚪"
        lines.append(f"{mark} {uni.get(c, c)} {c}　{d['label']}　{d['pct']:+.2f}%")
        if d["st"] in ("critical", "nopush"):
            alarm.append(f"{uni.get(c, c)}：{d['rx']}")
    out = "【我的主治病人】\n" + "\n".join(lines)
    if alarm:
        out += "\n需要注意：" + "；".join(alarm)
    return out


def weekly(data, hist, matcher):
    """院長週報：最近 5 個新聞日的科別聲量、本週病人漲跌、診斷命中率。"""
    news = data["news"]
    ds = sorted({n["date"] for n in news if n.get("date")})[-5:]
    if len(ds) < 3:
        return None
    cnt, named = {}, {}
    for n in news:
        if n.get("date") not in ds:
            continue
        codes, th = matcher.match(n["t"])
        for t in th:
            if t not in matcher.glob:
                cnt[t] = cnt.get(t, 0) + 1
        for c in codes:
            named[c] = named.get(c, 0) + 1
    top = sorted(cnt.items(), key=lambda x: -x[1])[:5]
    moves = []
    if hist and hist.get("dates"):
        hd = hist["dates"]
        i0 = max(0, len(hd) - 6)
        for c, k in named.items():
            arr = hist["close"].get(c)
            if arr and arr[i0] and arr[-1]:
                moves.append((c, (arr[-1] / arr[i0] - 1) * 100, k))
    moves.sort(key=lambda x: -x[1])
    uni = {r.split(",")[0]: r.split(",")[1] for r in data["uni_raw"].split(";")}
    cases = []
    try:
        cases = case_conference(data, hist, matcher, ds)
    except Exception as e:  # noqa: BLE001
        U.note(f"病例討論會產生失敗（{e}）")
    return {"from": ds[0], "to": ds[-1], "themes": top, "cases": cases,
            "best": [(uni.get(c, c), c, round(p, 1)) for c, p, _ in moves[:5]],
            "worst": [(uni.get(c, c), c, round(p, 1)) for c, p, _ in moves[-5:][::-1]],
            "news": sum(1 for n in news if n.get("date") in ds)}


def weekly_text(w, site):
    if not w:
        return ""
    s = [f"🏥 812拉基醫院｜院長週報 {w['from']}～{w['to']}",
         f"本週掛號 {w['news']} 則新聞。",
         "【最熱科別】" + "、".join(f"{t}科 {n} 則" for t, n in w["themes"]),
         "【本週好轉】" + "、".join(f"{n} {p:+.1f}%" for n, _, p in w["best"]),
         "【本週惡化】" + "、".join(f"{n} {p:+.1f}%" for n, _, p in w["worst"])]
    if w.get("cases"):
        s.append("")
        s.append("【病例討論會】")
        for i, c in enumerate(w["cases"], 1):
            s.append(f"{i}. {c['title']}：{c['n']}（{c['c']}）")
            s.append(f"   {c['story']}")
    s += ["", "以上為新聞與行情的觀察整理，不構成投資建議。", site]
    return "\n".join(s)


# ---------------------------------------------------------------- 申報組：公司重大訊息
MOPS_SRC = [("https://openapi.twse.com.tw/v1/opendata/t187ap04_L", "mops_twse.json", "上市"),
            ("https://www.tpex.org.tw/openapi/v1/mopsfe_t187ap04_O", "mops_tpex.json", "上櫃"),
            ("https://www.tpex.org.tw/openapi/v1/t187ap04_O", "mops_tpex2.json", "上櫃")]


def get_mops(prev, keep_days=5, cap=400):
    """證交所／櫃買中心每日重大訊息。回傳 [{d, tm, c, n, t, k, mk}]，新的在前；抓不到就沿用上一版。"""
    out, got_otc = [], False
    for url, key, mk in MOPS_SRC:
        if mk == "上櫃" and got_otc:
            continue
        try:
            rows = json.loads(U.fetch(url, key, tries=1, timeout=20))
        except Exception as e:  # noqa: BLE001
            if mk == "上市":
                U.note(f"上市重大訊息抓取失敗（{str(e)[:60]}）")
            continue
        if not isinstance(rows, list):
            continue
        for r in rows:
            subj = next((str(v) for k, v in r.items() if "主旨" in k), "").strip()
            c = str(r.get("公司代號") or r.get("SecuritiesCompanyCode") or "").strip()
            d = U.roc_to_iso(r.get("發言日期") or r.get("Date") or "")
            if not (c and d and subj):
                continue
            tm = re.sub(r"\D", "", str(r.get("發言時間") or "")).zfill(6)
            out.append({"d": d, "tm": f"{tm[:2]}:{tm[2:4]}", "c": c, "n": str(r.get("公司名稱") or "").strip(),
                        "t": subj[:120], "k": str(r.get("符合條款") or "").strip(), "mk": mk})
        if mk == "上櫃" and rows:
            got_otc = True
    if not out:
        return prev
    seen, merged = set(), []
    for m in out + (prev or []):
        k = (m["c"], m["d"], m["t"])
        if k not in seen:
            seen.add(k)
            merged.append(m)
    days = sorted({m["d"] for m in merged})[-keep_days:]
    merged = [m for m in merged if m["d"] in days]
    merged.sort(key=lambda m: (m["d"], m["tm"]), reverse=True)
    return merged[:cap]


# ---------------------------------------------------------------- 資訊室：各資料來源的燈號
SOURCE_GROUPS = [
    ("證交所", ("isin_twse", "twse_", "mi_", "t86_", "rev_twse", "mops_twse")),
    ("櫃買中心", ("isin_tpex", "tpex_", "rev_tpex", "mops_tpex")),
    ("公開資訊觀測站", ("rev_mops",)),
    ("Yahoo 股市新聞", ("rss_0_",)),
    ("Google 新聞", ("rss_1_",)),
    ("鉅亨網", ("cnyes",)),
    ("美股報價", ("us_", "stooq")),
    ("總經指標", ("macro_", "fred_")),
]


def source_status(log):
    """把這次每個抓取結果歸到資料來源，算出成功數與失敗原因。"""
    out = []
    for name, pres in SOURCE_GROUPS:
        rows = [x for x in log if x[0].startswith(pres)]
        if not rows:
            continue
        ok = sum(1 for x in rows if x[1])
        errs = [f"{k}：{e}" for k, good, e in rows if not good][:4]
        out.append({"n": name, "ok": ok, "all": len(rows), "err": errs})
    return out


# ---------------------------------------------------------------- 病例討論會：本週最值得學的 3 個案例
def case_conference(data, hist, matcher, ds):
    """挑出消息與股價走勢最不一致、或公司出面澄清的案例，寫成病例報告。"""
    if not hist or not hist.get("dates"):
        return []
    hd, close = hist["dates"], hist["close"]
    uni = {r.split(",")[0]: r.split(",")[1] for r in data["uni_raw"].split(";")}

    def ret_from(c, d0):
        arr = close.get(c)
        if not arr:
            return None
        i = next((k for k, d in enumerate(hd) if d >= d0), None)
        if i is None or i < 1 or arr[i - 1] is None or arr[-1] is None:
            return None
        return (arr[-1] / arr[i - 1] - 1) * 100

    net, first, head = {}, {}, {}
    for n in data["news"]:
        if n.get("date") not in ds or not n.get("sent"):
            continue
        codes, _ = matcher.match(n["t"])
        for c in codes:
            w = n["sent"] * n.get("heat", 3)
            net[c] = net.get(c, 0) + w
            first[c] = min(first.get(c, n["date"]), n["date"])
            if c not in head or abs(w) > head[c][0]:
                head[c] = (abs(w), n["t"])
    rows = []
    for c, v in net.items():
        r = ret_from(c, first[c])
        if r is not None:
            rows.append((c, v, r))
    cases = []
    nopush = sorted([x for x in rows if x[1] >= 6 and x[2] <= -3], key=lambda x: x[2])
    if nopush:
        c, v, r = nopush[0]
        cases.append({"k": "nopush", "title": "利多滿天飛，股價卻下跌", "c": c, "n": uni.get(c, c), "net": v, "ret": round(r, 1),
                      "news": head[c][1][:60],
                      "story": f"本週利多新聞情緒淨值 +{v}，但從第一則新聞到現在股價 {r:+.1f}%。好消息可能早已反映，或市場不相信；這類病人追高要特別小心。"})
    resist = sorted([x for x in rows if x[1] <= -6 and x[2] >= 3], key=lambda x: -x[2])
    if resist:
        c, v, r = resist[0]
        cases.append({"k": "resist", "title": "利空纏身，股價反而上漲", "c": c, "n": uni.get(c, c), "net": v, "ret": round(r, 1),
                      "news": head[c][1][:60],
                      "story": f"本週利空新聞情緒淨值 {v}，股價卻 {r:+.1f}%。壞消息可能已經出盡，常是底部訊號；可以觀察是否量縮止穩。"})
    for m in data.get("mops") or []:
        if len(cases) >= 3:
            break
        if m.get("d") in ds and re.search(r"澄清|媒體報導", m.get("t", "")) and m["c"] not in [x["c"] for x in cases]:
            r = ret_from(m["c"], m["d"])
            if r is None:
                continue
            cases.append({"k": "clar", "title": "公司出面澄清新聞", "c": m["c"], "n": uni.get(m["c"], m["c"]), "ret": round(r, 1),
                          "news": m["t"][:60],
                          "story": f"{m['d'][5:].replace('-', '/')} 公司申報澄清，之後股價 {r:+.1f}%。{'市場接受澄清，股價回穩。' if r > 0 else '澄清後股價仍走弱，市場可能還在疑慮。'}"})
    if len(cases) < 3:  # 不夠三個時，補上本週消息與股價最背離的
        left = sorted([x for x in rows if x[0] not in [y["c"] for y in cases] and x[1] * x[2] < 0], key=lambda x: -abs(x[1] * x[2]))
        for c, v, r in left[:3 - len(cases)]:
            cases.append({"k": "gap", "title": "消息與股價方向相反", "c": c, "n": uni.get(c, c), "net": v, "ret": round(r, 1),
                          "news": head[c][1][:60],
                          "story": f"本週新聞情緒淨值 {v:+}，股價卻 {r:+.1f}%。消息面與股價背離，代表市場另有看法，值得回頭檢討。"})
    return cases[:3]


# ---------------------------------------------------------------- 主動通報：主治病人出狀況時立刻推播
def _rev_flags(rev, uni_ind):
    """和網站檢驗科同一套規則：同產業中間一半的區間再往外放寬，超出標 H、低於標 L。"""
    by = {}
    for c, r in (rev or {}).items():
        if r and r[2] is not None and abs(r[2]) <= 900 and c in uni_ind:
            by.setdefault(uni_ind[c], []).append((c, r))
    out = {}
    for rs in by.values():
        if len(rs) < 5:
            continue
        ys = sorted(x[1][2] for x in rs)

        def q(p):
            i = (len(ys) - 1) * p
            lo, hi = int(i), min(len(ys) - 1, int(i) + 1)
            return ys[lo] + (ys[hi] - ys[lo]) * (i - lo)
        q1, q3, md = q(.25), q(.75), q(.5)
        iqr = max(8, q3 - q1)
        for c, r in rs:
            y = r[2]
            if y > q3 + iqr and y - md >= 15:
                out[c] = ("H", y, r[0], md)
            elif y < q1 - iqr and md - y >= 15:
                out[c] = ("L", y, r[0], md)
    return out


def alerts(data, hist, prev_sent):
    """回傳 (要推播的文字, 新的已通報清單)。同一件事只通報一次。"""
    codes = watch_list()
    if not codes:
        return "", prev_sent or []
    sent = list(prev_sent or [])
    uni = {}
    for row in data["uni_raw"].split(";"):
        c, n, _, i = row.split(",")
        uni[c] = (n, data["inds"][int(i)])
    flags = _rev_flags(data.get("rev"), {c: v[1] for c, v in uni.items()})
    recent = sorted({m["d"] for m in data.get("mops") or []})[-2:]
    msgs, new = [], []
    for c in codes:
        name = uni.get(c, (c,))[0]
        for m in data.get("mops") or []:
            if m["c"] == c and m["d"] in recent and re.search(r"澄清|媒體報導", m["t"]):
                k = f"clar:{c}:{m['d']}"
                if k not in sent:
                    new.append(k)
                    msgs.append(f"📢 {name}（{c}）公司出面澄清：{m['t'][:50]}")
        if c in flags:
            f, y, ym, md = flags[c]
            k = f"rev:{c}:{ym}"
            if k not in sent:
                new.append(k)
                msgs.append(f"🧪 {name}（{c}）{ym[:-2]}/{ym[-2:]} 月營收年增 {y:+.1f}%，同業中位數 {md:+.1f}%，檢驗科標「{'H 偏高' if f == 'H' else 'L 偏低'}」")
        arr = ((hist or {}).get("inst") or {}).get(c)
        idates = (hist or {}).get("inst_dates") or []
        if arr:
            n, i = 0, len(arr) - 1
            while i >= 0 and arr[i] is not None and arr[i] < 0:
                n, i = n + 1, i - 1
            if n >= 5:
                k = f"sell:{c}:{idates[i + 1] if i + 1 < len(idates) else ''}"
                if k not in sent:
                    new.append(k)
                    msgs.append(f"🚨 {name}（{c}）外資加投信已連續賣超 {n} 天，家屬撤離中")
    if not msgs:
        return "", sent
    text = "🏥 812拉基醫院｜主治病人緊急通報\n\n" + "\n".join(msgs) + "\n\n以上只是觀察重點，不構成投資建議。"
    return text, (sent + new)[-300:]
