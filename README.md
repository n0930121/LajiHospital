# 812拉基醫院

把台股新聞畫成醫院平面圖：挑兩個題材當作兩棟病房，每則新聞是一位醫師，新聞點名的股票是病人，最新收盤漲跌就是病人的生命徵象。網站放在 GitHub Pages，由 GitHub Actions 每個交易日自動更新資料。

## 資料夾

| 位置 | 用途 |
|---|---|
| `docs/index.html` | 網頁本體（GitHub Pages 從 `docs` 資料夾發布） |
| `docs/data.json` | 每日更新的資料：公司清單、行情、加權指數、新聞 |
| `docs/lexicon.json` | 題材詞庫、族群名（如「重電四雄」）、公司簡稱、容易誤判的公司名 |
| `docs/engine.js` | 新聞與股票的比對規則，網頁使用 |
| `scripts/update.py` | 每日更新腳本 |
| `config.json` | 新聞來源、搜尋關鍵字、利多利空判讀詞 |
| `.github/workflows/update.yml` | 自動排程 |
| `tests/fixtures/` | 離線測試用的樣本資料 |

## 第一次設定（約 5 分鐘）

1. 在 GitHub 建立新的 repository（例如 `laji-hospital`），把這個資料夾全部上傳。
2. 到 **Settings → Pages**，Source 選 **Deploy from a branch**，Branch 選 `main`、資料夾選 `/docs`，按 Save。
3. 到 **Settings → Actions → General**，最下面 **Workflow permissions** 選 **Read and write permissions**，按 Save。這樣排程才能把新資料存回 repository。
4. 到 **Actions** 分頁，左側點「每日更新資料」，按 **Run workflow** 手動跑第一次。
5. 約 1 分鐘後打開 `https://<你的帳號>.github.io/<repository 名稱>/` 就能看到網站。

## 自動更新的時間

週一到週五（台北時間）：

- **08:17**：開盤前，更新新聞
- **15:23**：收盤後，更新行情與新聞
- **18:23**：再補一次，確保證交所當日資料已上架

休市日證交所沒有新行情，網站會沿用最近一個交易日的收盤，新聞照常更新。

## 更新腳本做了什麼

1. **公司清單**：從證交所 ISIN 公告頁抓上市、上櫃、創新板股票的代號、名稱與產業別。
2. **行情**：證交所與櫃買中心 OpenAPI 的每日收盤價、漲跌與成交值。
3. **加權指數**：證交所 OpenAPI。
4. **新聞**：
   - **來源**：Yahoo 股市 RSS、Google 新聞 RSS（依 `config.json` 的關鍵字搜尋）、鉅亨網台股新聞。
   - **篩選**：只留標題裡有提到上市櫃公司或題材詞的新聞。
   - **合併**：不同來源的同一則新聞會合併，被越多家報導，熱度越高。
   - **利多利空**：依 `config.json` 的關鍵字加權判讀，例如「漲停」「創高」算利多，「跌停」「下修」算利空。
   - **保留期間**：近 10 天，最多 160 則。舊新聞保留原本的判讀，不會每天被改寫。
5. **出錯時**：任何來源失敗，該項就沿用上一版資料，網站不會壞掉。失敗原因寫在 `data.json` 的 `notes`，滑鼠移到網頁上方的更新時間就看得到。

網頁上的股票比對與題材分類，是在瀏覽器裡用 `lexicon.json` 即時算的。改詞庫後重新整理網頁就生效，不必等排程。

## 常見調整

- **新增題材**：在 `docs/lexicon.json` 的 `themes` 加一行，例如 `"矽智財": ["矽智財", "IP 授權"]`。也記得在 `config.json` 的 Google 新聞 `queries` 加搜尋詞。
- **公司名被誤判**：例如「大量」「統一」這種同時是常用詞的名稱，加到 `ambiguous`。之後只有寫成「大量（3167）」才會被認出來。
- **調整利多利空**：改 `config.json` 的 `sentiment`，數字是權重。
- **改更新時間**：改 `.github/workflows/update.yml` 的 `cron`。注意它用 UTC，台北時間要減 8 小時。

## 本機測試

```bash
pip install requests
python scripts/update.py                                 # 實際抓取
python scripts/update.py --offline tests/fixtures --out /tmp/test.json   # 用樣本資料測試
cd docs && python -m http.server 8000                    # 打開 http://localhost:8000
```

網頁要用 `http://` 開，直接雙擊 `index.html` 會因為瀏覽器限制讀不到 `data.json`。

## 注意

- 利多利空與熱度是關鍵字規則判讀，只能參考，不是投資建議。
- 新聞只存標題、來源與連結，不轉載內文。
- GitHub 會在 repository 60 天沒有任何活動時暫停排程。這個專案每天都有自動提交，正常不會發生；如果真的被暫停，到 Actions 分頁按一下啟用即可。
