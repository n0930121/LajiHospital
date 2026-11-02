"""診斷用：存下產業價值鏈頁面原始 HTML，用來寫解析程式。"""
import json
import requests

H = {"User-Agent": "Mozilla/5.0 (812-laji-hospital probe)"}
out = {}
for ic in ["P000", "E000", "6000"]:
    r = requests.get(f"https://ic.tpex.org.tw/introduce.php?ic={ic}", headers=H, timeout=40)
    r.encoding = "utf-8"
    out[ic] = r.text
r = requests.get("https://ic.tpex.org.tw/", headers=H, timeout=40)
r.encoding = "utf-8"
out["home"] = r.text
json.dump(out, open("probe.json", "w", encoding="utf-8"), ensure_ascii=False)
print({k: len(v) for k, v in out.items()})
