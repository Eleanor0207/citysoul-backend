"""
產生 `contracts/openapi.json` —— 後端與 `citysoul-client` 之間的正式契約。

用法：

    uv run python -m scripts.generate_openapi_contract

改過任何 Pydantic 模型或路由的 `responses=` 之後都要重跑，否則
`tests/test_api_contract.py` 的「快照未過期」那條會紅。

## 這份檔案是唯一真相

`citysoul-client` **不放副本**，只放一個記錄來源版本的 lock 檔。快照進 git 是
刻意的：任何一次契約變動都會出現在 code review 的 diff 裡，而不是隱形發生。

## 為什麼要固定排序與格式

`json.dumps(sort_keys=True, indent=2)` 加結尾換行。少了這個，每次重新產生都會
因為 dict 順序或縮排差異冒出大量無意義的 diff，code review 就失去意義了——而
「契約變動看得見」正是這份快照存在的全部理由。

`ensure_ascii=False` 讓中文的 description 在 diff 裡是可讀的中文，不是
`\\u57ce\\u5e02` 這種東西。

## OpenAPI 版本維持 3.1.0（已拍板，勿降級）

曾評估降級到 3.0 以提高工具相容性，結論是不可行且有害：FastAPI 的版本字串只是
原封不動塞進輸出，**改它不會改 schema 內容**。schema 由 Pydantic v2 依 JSON
Schema 2020-12 產生，nullable 欄位固定是 `anyOf: [{$ref}, {type: null}]`，而
`type: null` 在 3.0 中不合法。宣告 3.0 只會產出一份自稱 3.0、內容卻是 3.1 的
無效文件，工具照 3.0 規則解析可能靜默產出錯誤 DTO——比誠實的 3.1 更糟。

若日後 codegen 真的需要 3.0，正解是後處理走真正的 3.1→3.0 轉換器。
"""
import json
from pathlib import Path

from app.main import app

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "contracts" / "openapi.json"


def render_contract() -> str:
    """產出契約的正規化 JSON 文字。測試與這支腳本共用同一份，不各寫一次。"""
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT_PATH.write_text(render_contract(), encoding="utf-8")
    print(f"contract written to {CONTRACT_PATH}")


if __name__ == "__main__":
    main()
