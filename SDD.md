# 規格文件在哪裡

**系統設計文件已移至獨立的文件庫：**

## → https://github.com/city-soul-taipei/citysoul-doc

| 文件                             | 說明                             |
| -------------------------------- | -------------------------------- |
| `SDD_v2.2_Unity_3D.md`         | 系統設計文件，**單一權威** |
| `citysoul_data_schema.md`      | PostgreSQL 完整 schema           |
| `SDD-v2.1-Overview.html`       | 架構總覽（視覺化，內容仍為 v2.1）|
| `dev_status/`                  | Phase 交付清單、排程對應表       |
| `archive/`                     | 已封存的 v1 文件，**不得作為實作依據** |

## 後端最常用到的章節

| 找什麼 | 去哪 |
|---|---|
| 業務規則（距離、token、任務、共鳴值、配額） | SDD **§18** |
| 驗收標準（AC 逐條） | SDD **§19** |
| 內容生產管線（當日情境、引導提問、任務目錄） | SDD **§20** |
| 模組邊界與人格卡注入硬規則 | SDD §6、**§6.5** |
| API 契約語意 | SDD **§18.9**（機器可讀版本在本 repo `contracts/openapi.json`） |

## 為什麼不放在這裡

先前這個 repo 的根目錄有一份 SDD 副本，本機 `docs/` 也有一份，結果**兩份真的分歧了**
——一份有輪詢間隔改 10 秒，另一份有 §7.3.1 灰模交付規格，各自都缺對方的內容。

一份會漂移的規格副本，比一份需要多點一次連結的規格糟糕得多。

## ⚠️ `docs/` 底下的 `city_soul_AR_*.md` 已封存

那批文件曾經是業務規則與驗收標準的權威，但它們被 `.gitignore` 排除（`docs/*`），
等於權威只活在單一台機器上。SDD v2.2 已將內容吸收進 §18／§19，原檔封存於
`citysoul-doc/archive/`。

它們讀起來像規格、寫得也很詳細，但描述的是 Flutter 客戶端、viseme 時間軸、
任務失敗重試上限這些**已被推翻**的設計。照著實作是浪費一整個 Sprint 最快的方式。

例外：`city_soul_AR_landmark_recognition.md`（B13 方案）仍是現行權威，已移至
`citysoul-doc/` 根目錄。

## 這個 repo 仍然持有

- `CONTEXT.md` — 產品詞彙表（與 SDD 互補，不重複）
- `contracts/openapi.json` — API 契約，**由本 repo 的程式碼自動產生**，所以留在這裡
- `docs/adr/` — 架構決策記錄
- `docs/content-governance/` — 人格卡審核軌跡
- `docs/` 其餘內容為本機專用或已封存（見 `.gitignore`）
