"""
重新產生 `contracts/openapi.json`（#30：API 契約單一真相來源）。

用法：
    uv run python -m scripts.export_openapi

輸出**穩定排序、格式固定**的 JSON（`sort_keys=True`）——否則每次重新產生都
會出現跟內容無關的 diff（例如欄位順序洗牌），讓 code review 的 diff 失去
意義。這支腳本本身不需要資料庫連線，`app.openapi()` 只讀路由與 Pydantic
model 定義。
"""
import json
from pathlib import Path

from app.main import app

_CONTRACT_PATH = Path(__file__).resolve().parent.parent / "contracts" / "openapi.json"


def render_contract() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    _CONTRACT_PATH.parent.mkdir(exist_ok=True)
    _CONTRACT_PATH.write_text(render_contract())
    print(f"寫入 {_CONTRACT_PATH}")


if __name__ == "__main__":
    main()
