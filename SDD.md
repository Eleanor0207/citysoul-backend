# 規格文件在哪裡

**系統設計文件已移至獨立的文件庫：**

## → https://github.com/city-soul-taipei/citysoul-doc

| 文件 | 說明 |
|---|---|
| `SDD_v2.1_Unity_3D.md` | 系統設計文件，**單一權威** |
| `SDD-v2.1-Overview.html` | 架構總覽（視覺化） |
| `Phase1_交付清單與驗收標準.md` | Phase 1 交付項目與驗收指令 |

## 為什麼不放在這裡

先前這個 repo 的根目錄有一份 SDD 副本，本機 `docs/` 也有一份，結果**兩份真的分歧了**
——一份有輪詢間隔改 10 秒，另一份有 §7.3.1 灰模交付規格，各自都缺對方的內容。

一份會漂移的規格副本，比一份需要多點一次連結的規格糟糕得多。

## 這個 repo 仍然持有

- `CONTEXT.md` — 產品詞彙表（與 SDD 互補，不重複）
- `contracts/openapi.json` — API 契約，**由本 repo 的程式碼自動產生**，所以留在這裡
- `docs/adr/` — 架構決策記錄
- `docs/` 其餘內容為本機專用（見 `.gitignore`）
