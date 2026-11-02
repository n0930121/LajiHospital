"""診斷用：檢查櫃買中心產業價值鏈資訊平台能不能抓、格式長什麼樣。"""
import json
import re
import requests

H = {"User-Agent": "Mozilla/5.0 (812-laji-hospital probe)"}
out = {}


def get(name, url):
    try:
        r = requests.get(url, headers=H, timeout=40)
        r.encoding = r.apparent_encoding or "utf-8"
        t = r.text
        links = sorted(set(re.findall(r'href="([^"]*(?:ic=|stk_code=|introduce|chain)[^"]*)"', t)))[:80]
        out[name] = {"url": url, "status": r.status_code, "len": len(t), "title": (re.search(r"<title>(.*?)</title>", t, re.S) or [None, ""])[1].strip()[:100],
                     "links": links, "text": re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", t, flags=re.S))[:2500],
                     "json_like": t[:400] if t.lstrip().startswith(("{", "[")) else None}
    except Exception as e:  # noqa: BLE001
        out[name] = {"url": url, "error": str(e)[:200]}


get("home", "https://ic.tpex.org.tw/")
get("index", "https://ic.tpex.org.tw/index.php")
get("ic_pcb", "https://ic.tpex.org.tw/introduce.php?ic=E000")
get("ic_semi", "https://ic.tpex.org.tw/introduce.php?ic=D000")
get("co_4526", "https://ic.tpex.org.tw/company_chain.php?stk_code=4526")
get("co_6234", "https://ic.tpex.org.tw/company_chain.php?stk_code=6234")
get("co_2330", "https://ic.tpex.org.tw/company_chain.php?stk_code=2330")
json.dump(out, open("probe.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print({k: (v.get("status"), v.get("len"), v.get("title")) for k, v in out.items()})
