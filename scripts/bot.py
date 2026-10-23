#!/usr/bin/env python3
"""812拉基醫院 Telegram 掛號機器人。

由 GitHub Actions 每 10 分鐘執行一次，讀取你傳給機器人的訊息並回覆：
  2330 或 台積電     → 回傳該股的綜合診斷
  /watch 2330        → 加入我的主治病人（每天收盤推播時會附上它們的狀況）
  /unwatch 2330      → 移除
  /list              → 列出我的主治病人
  /report            → 今天的早晨交班報告
  /help              → 使用說明
只回覆 TELEGRAM_CHAT_ID 這個聊天室，其他人傳訊息一律忽略。
"""
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update as U  # noqa: E402
import extras as X  # noqa: E402

X.U = U
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT = str(os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
API = f"https://api.telegram.org/bot{TOKEN}"
SITE = "https://n0930121.github.io/LajiHospital/"
WATCH = os.path.join(U.ROOT, "docs", "watch.json")

HELP = """🏥 812拉基醫院 掛號櫃台
傳股票代號或名稱（例如 2330 或 台積電），我會回你綜合診斷。

/watch 2330　加入我的主治病人
/unwatch 2330　移除主治病人
/list　查看我的主治病人
/report　今天的早晨交班報告

我每 10 分鐘看一次訊息，回覆可能要等幾分鐘。
診斷只是觀察重點，不構成投資建議。"""


def send(text):
    requests.post(f"{API}/sendMessage", json={"chat_id": CHAT, "text": text[:4000], "disable_web_page_preview": True}, timeout=20)


def load():
    data = json.load(open(os.path.join(U.ROOT, "docs", "data.json"), encoding="utf-8"))
    lex = json.load(open(U.LEX, encoding="utf-8"))
    mks, inds = data["mks"], data["inds"]
    uni = [[c, n, mks[int(m)], inds[int(i)]] for c, n, m, i in (r.split(",") for r in data["uni_raw"].split(";"))]
    return data, lex, uni, X.Matcher(uni, lex)


def find_code(q, uni):
    q = q.strip()
    for c, n, *_ in uni:
        if q == c or q == n:
            return c
    hits = [c for c, n, *_ in uni if q in n]
    return hits[0] if len(hits) == 1 else (hits[0] if hits else None)


def diagnosis_text(code, data, uni, lex, matcher):
    info = next((u for u in uni if u[0] == code), [code, code, "", ""])
    d = X.diagnose_py(code, data, X.news_index(data["news"], matcher))
    q = d["q"]
    lines = [f"🩺 {info[1]}（{code}）診斷", f"病房：{info[2]}　{info[3]}"]
    if q:
        lines.append(f"生命徵象：{data.get('asof', '')} 收盤 {q[0]:,}　{d['pct']:+.2f}%")
    lines.append(f"診斷：{d['label']}")
    lines.append(f"醫囑：{d['rx']}")
    if d["n"]:
        lines.append(f"主訴：近 3 天 {d['n']} 則新聞，情緒淨值 {d['net']:+}")
        for n in d["news"][:3]:
            lines.append(f"　・{n['t'][:40]}（{'利多' if n['sent'] > 0 else '利空' if n['sent'] < 0 else '中性'}）")
    if d["inst"]:
        f, t = d["inst"][0], d["inst"][1]
        lines.append(f"家屬探病：外資 {f:+,} 張、投信 {t:+,} 張")
    rv = (data.get("rev") or {}).get(code)
    if rv and rv[2] is not None:
        lines.append(f"檢驗報告：{rv[0][:-2]}/{rv[0][-2:]} 月營收年增 {rv[2]:+.1f}%")
    us = [u for u in lex.get("us", []) if code in u["tw"]]
    snaps = [(u, (data.get("us") or {}).get(u["t"])) for u in us]
    snaps = [x for x in snaps if x[1]]
    if snaps:
        lines.append("海外病史：" + "、".join(f"{u['n']} {s['pct']:+.2f}%" for u, s in snaps[:3]))
    lines += ["", f"看床頭卡：{SITE}#s{code}", "以上只是觀察重點，不構成投資建議。"]
    return "\n".join(lines)


def save_watch(codes):
    with open(WATCH, "w", encoding="utf-8") as f:
        json.dump({"codes": codes}, f, ensure_ascii=False)


def main():
    if not TOKEN or not CHAT:
        print("沒有設定 Telegram，略過")
        return
    r = requests.get(f"{API}/getUpdates", params={"timeout": 0, "allowed_updates": json.dumps(["message"])}, timeout=30).json()
    ups = r.get("result", [])
    if not ups:
        print("沒有新訊息")
        return
    data, lex, uni, matcher = load()
    codes = X.watch_list()
    changed = False
    for u in ups:
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != CHAT:
            continue
        text = (m.get("text") or "").strip()
        if not text:
            continue
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        try:
            if cmd in ("/start", "/help"):
                send(HELP)
            elif cmd == "/list":
                send(X.watch_text(data, matcher) or "目前還沒有主治病人。傳 /watch 2330 試試看。")
            elif cmd == "/report":
                send(X.handover_text(data.get("report"), SITE) or "今天的交班報告還沒產生。")
            elif cmd in ("/watch", "/unwatch"):
                c = find_code(arg, uni) if arg else None
                if not c:
                    send(f"找不到「{arg}」，請輸入股票代號或完整名稱，例如 /watch 2330")
                elif cmd == "/watch":
                    if c not in codes:
                        codes.append(c)
                        changed = True
                        save_watch(codes)
                    send(f"✅ 已把 {dict((x[0], x[1]) for x in uni)[c]}（{c}）列為你的主治病人，每天收盤推播會附上它的診斷。")
                else:
                    if c in codes:
                        codes.remove(c)
                        changed = True
                        save_watch(codes)
                    send(f"已移除 {c}。")
            else:
                c = find_code(text, uni)
                send(diagnosis_text(c, data, uni, lex, matcher) if c else f"掛號櫃台找不到「{text}」。請傳股票代號（例如 2330）或名稱，或傳 /help 看說明。")
        except Exception as e:  # noqa: BLE001
            send(f"處理時發生錯誤：{e}")
    # 告訴 Telegram 這些訊息已經處理過
    requests.get(f"{API}/getUpdates", params={"offset": ups[-1]["update_id"] + 1, "timeout": 0}, timeout=30)
    if changed:
        save_watch(codes)
        print("主治病人清單已更新")


if __name__ == "__main__":
    main()
