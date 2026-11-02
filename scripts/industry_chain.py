"""櫃買中心「產業價值鏈資訊平台」（https://ic.tpex.org.tw/）→ docs/ic.json

官方整理的產業鏈：每條鏈分上游、中游、下游，每段有細項，細項底下列出上市櫃公司。
網站把同一條鏈、相關環節的公司歸在一起，用來判斷「哪些股票有關」，不靠新聞、也不靠推測。
資料一週更新一次就夠（公司所屬產業鏈很少變）。
"""
import html
import json
import os
import re

BASE = "https://ic.tpex.org.tw/"


def _text(s):
    s = re.sub(r"<br\s*/?>", "", s)
    s = re.sub(r"<font.*?</font>", "", s, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def parse_chain(page):
    """解析一條產業鏈頁面 → (鏈名稱, [{id, n, pos, codes}])"""
    m = re.search(r"<title>.*?&gt;\s*(.*?)產業鏈簡介", page, re.S) or re.search(r"<title>.*?>\s*(.*?)產業鏈簡介", page, re.S)
    name = html.unescape(m.group(1)).strip() if m else ""
    # 每個環節屬於上游、中游或下游：依環節方塊前面最近的標題判斷
    pos_of = {}
    main = page.split('id="main_ic_panel"', 1)[1] if 'id="main_ic_panel"' in page else page
    main = main.split('id="companyList_', 1)[0]
    cur = ""
    for tok in re.finditer(r'class="chain-title-panel"[^>]*>(.*?)</div>|id="ic_link_([0-9A-Z]+)"[^>]*>(.*?)</div>\s*(?:</div>)?', main, re.S):
        if tok.group(1) is not None:
            cur = _text(tok.group(1))
        else:
            pos_of[tok.group(2)] = cur
    segs = []
    for m in re.finditer(r'<div id="companyList_([0-9A-Z]+)" title="([^"]*)"[^>]*>(.*?)<span class="company-number">', page, re.S):
        sid, title, body = m.group(1), html.unescape(m.group(2)).strip(), m.group(3)
        codes = sorted(set(re.findall(r"company_basic\.php\?stk_code=(\d{4})\b", body)))
        pos = pos_of.get(sid)
        if pos is None:  # 子細項：找編號最接近的上層環節
            for k in sorted(pos_of, key=len, reverse=True):
                if sid.rstrip("0").startswith(k.rstrip("0")) or k.rstrip("0").startswith(sid.rstrip("0")[:2]):
                    pos = pos_of[k]
                    break
        if any(x["id"] == sid for x in segs):   # 同一個細項在頁面上會出現兩次
            continue
        segs.append({"id": sid, "n": title, "pos": pos or "", "codes": codes})
    return name, segs


def build(fetch, note=print):
    """抓全部產業鏈。fetch(url, key) 由 update.py 提供（含逾時保護與離線模式）。"""
    def get(url, key):  # 網頁是 UTF-8，但沒在標頭寫明，要自己指定，否則中文會變亂碼
        return fetch(url, key, binary=True, tries=2, timeout=40).decode("utf-8", "ignore")
    home = get(BASE, "ic_home.html")
    ics = sorted(set(re.findall(r"introduce\.php\?ic=([0-9A-Z]{4})\b", home)))
    from concurrent.futures import ThreadPoolExecutor

    def one(ic):
        try:
            name, segs = parse_chain(get(f"{BASE}introduce.php?ic={ic}", f"ic_{ic}.html"))
        except Exception as e:  # noqa: BLE001
            note(f"產業鏈 {ic} 抓取失敗（{str(e)[:60]}）")
            return None
        segs = [s for s in segs if s["codes"]]
        return {"ic": ic, "n": name, "segs": segs} if name and segs else None
    with ThreadPoolExecutor(4) as ex:   # 一次抓 4 頁，約 45 頁
        chains = [c for c in ex.map(one, ics) if c]
    print(f"官方產業鏈：首頁列出 {len(ics)} 條，解析成功 {len(chains)} 條")
    return chains


def update(fetch, path, note=print, max_age_days=7, today=None):
    """docs/ic.json 超過 7 天才重抓；抓失敗就保留舊的。"""
    old = None
    if os.path.exists(path):
        try:
            old = json.load(open(path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            old = None
    if old and today and old.get("updated"):
        import datetime as dt
        age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(old["updated"])).days
        if age < max_age_days:
            print(f"官方產業鏈：{old['updated']} 已更新過，沿用")
            return old
    chains = build(fetch, note)
    if len(chains) < 10:
        note(f"官方產業鏈只抓到 {len(chains)} 條，保留上一版")
        return old
    data = {"updated": today, "src": BASE, "chains": chains}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    n = len({c for ch in chains for s in ch["segs"] for c in s["codes"]})
    print(f"官方產業鏈：{len(chains)} 條、涵蓋 {n} 家上市櫃公司")
    return data
