"""
法律文件頁：隱私權政策與服務條款。

## 為什麼掛在後端而不是另開一個網站

Google Play 上架要求隱私權政策有一個公開、免登入的網址，App 內「設定 → 關於」
也要點得開。既有的 Cloud Run 服務已經有 HTTPS 與一個穩定網址，多兩條路由的成本
是零；另外開 Firebase Hosting 或 Cloud Storage + Load Balancer 都要先有自有網域，
而網域還沒買（權衡見 `docs/legal/hosting.md`）。

⚠️ **這是暫時的。** Cloud Run 的預設網址帶著服務名稱與雜湊，重建服務時會變，
而**填進 Google Play 的隱私權政策網址不該再變**。所以在買到網域之前，
這裡的網址只能給 App 內部用，不要填進任何商店頁或對外文件。

## 正本是 Markdown，不是這裡的 HTML

`content/legal/privacy.md`、`content/legal/terms.md` 是唯一正本，
法務要改就改那兩個檔。HTML 是啟動時渲染出來的，沒有第二份可以改。

放 `content/` 而不是 `app/` 的理由很實際：`Dockerfile` 已經 `COPY content ./content`，
不必再加一行——而漏加那一行的錯誤在這個 repo 已經發生過兩次（見 Dockerfile 註解）。

## 渲染在啟動時做一次

文件不會在執行期改變，每個請求重新讀檔與轉換是白費工。轉好的 HTML 放在模組層級的
快取裡，`GET` 只是查表。副作用是**改了 Markdown 要重啟服務**——對一份一年改不到
兩次的文件，這個代價比每次請求都轉一遍划算。
"""

from __future__ import annotations

import logging
from pathlib import Path

import markdown
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

# app/modules/legal/router.py → 上溯四層是 repo 根目錄。
_CONTENT_DIR = Path(__file__).resolve().parents[3] / "content" / "legal"

# slug → (檔名, 頁面標題)。slug 就是網址最後一段。
_DOCUMENTS: dict[str, tuple[str, str]] = {
    "privacy": ("privacy.md", "隱私權政策"),
    "terms": ("terms.md", "服務條款"),
}

# 頁面樣式。刻意內嵌而不是連外部 CSS：這兩頁必須在任何網路環境下都讀得到，
# 而且色票跟 client 的 PaperTheme.uss 是同一組——法律文件看起來仍然是這個遊戲的一部分。
_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — 城市靈魂</title>
<style>
  :root {{
    --paper: rgb(248, 244, 236);
    --paper-deep: rgb(239, 230, 216);
    --accent: rgb(176, 83, 60);
    --brown: rgb(110, 97, 85);
    --border-color: rgb(211, 199, 181);
    --ink: rgb(56, 50, 46);
    --text-secondary: rgb(138, 126, 112);
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 2rem 1.25rem 5rem;
    background: var(--paper);
    color: var(--ink);
    font-family: "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", sans-serif;
    line-height: 1.85;
    -webkit-text-size-adjust: 100%;
  }}
  main {{ max-width: 44rem; margin: 0 auto; }}
  h1 {{ font-size: 1.6rem; margin: 0 0 1.5rem; color: var(--accent); }}
  h2 {{
    font-size: 1.2rem;
    margin: 2.5rem 0 0.75rem;
    padding-bottom: 0.4rem;
    border-bottom: 1px solid var(--border-color);
  }}
  h3 {{ font-size: 1.02rem; margin: 1.75rem 0 0.5rem; color: var(--brown); }}
  p, li {{ font-size: 0.97rem; }}
  a {{ color: var(--accent); }}
  strong {{ color: var(--accent); }}
  hr {{ border: none; border-top: 1px solid var(--border-color); margin: 2rem 0; }}
  blockquote {{
    margin: 1.5rem 0;
    padding: 0.75rem 1rem;
    background: var(--paper-deep);
    border-left: 3px solid var(--border-color);
    color: var(--text-secondary);
    font-size: 0.9rem;
  }}
  blockquote p {{ margin: 0.35rem 0; font-size: 0.9rem; }}
  code {{
    background: var(--paper-deep);
    padding: 0.1rem 0.3rem;
    border-radius: 3px;
    font-size: 0.9em;
  }}
  /* 表格在手機上一定會超出寬度，讓它自己捲，而不是把整頁撐寬。 */
  .table-scroll {{ overflow-x: auto; margin: 1rem 0; }}
  table {{ border-collapse: collapse; width: 100%; min-width: 32rem; font-size: 0.9rem; }}
  th, td {{
    border: 1px solid var(--border-color);
    padding: 0.5rem 0.7rem;
    text-align: left;
    vertical-align: top;
  }}
  th {{ background: var(--paper-deep); font-weight: 600; }}
  footer {{
    margin-top: 3.5rem;
    padding-top: 1.25rem;
    border-top: 1px solid var(--border-color);
    color: var(--text-secondary);
    font-size: 0.85rem;
  }}
  footer a {{ margin-right: 1rem; }}
</style>
</head>
<body>
<main>
{body}
<footer>
  <a href="/legal/privacy">隱私權政策</a>
  <a href="/legal/terms">服務條款</a>
  <div>© 程式靈魂工作室</div>
</footer>
</main>
</body>
</html>
"""


def _render(slug: str) -> str:
    filename, _title = _DOCUMENTS[slug]
    source = (_CONTENT_DIR / filename).read_text(encoding="utf-8")

    html = markdown.markdown(source, extensions=["tables", "sane_lists"])

    # python-markdown 產生的是裸 <table>，手機上會把整頁撐寬。包一層可捲的容器，
    # 讓超寬的表格自己捲。
    html = html.replace("<table>", '<div class="table-scroll"><table>')
    html = html.replace("</table>", "</table></div>")

    return html


def _build_cache() -> dict[str, str]:
    """
    啟動時把兩份文件都轉好。

    **檔案讀不到就讓它炸。** 這兩頁是上架的必要條件，靜默地少一頁比啟動失敗糟糕
    ——前者要等到有人回報「連結是 404」才會發現。
    """
    pages: dict[str, str] = {}
    for slug, (_filename, title) in _DOCUMENTS.items():
        pages[slug] = _PAGE_TEMPLATE.format(title=title, body=_render(slug))
        logger.info("legal: 已載入 %s", slug)
    return pages


_PAGES = _build_cache()


# `include_in_schema=False`：契約（contracts/openapi.json）是後端與 citysoul-client
# 之間的 API 約定，兩頁給人看的 HTML 不屬於那個約定。
router = APIRouter(prefix="/legal", tags=["legal"], include_in_schema=False)


@router.get("/{slug}", response_class=HTMLResponse)
def legal_document(slug: str) -> HTMLResponse:
    page = _PAGES.get(slug)
    if page is None:
        raise HTTPException(status_code=404, detail="沒有這份文件")

    # 文件一年改不到兩次，但也不能讓 CDN 快取到「舊版政策」還在線上。
    # 一小時是折衷：改完重新部署後，最久一小時內所有人都會看到新版。
    return HTMLResponse(page, headers={"Cache-Control": "public, max-age=3600"})
