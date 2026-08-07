"""
產生 `contracts/openapi.json`（issue #30）。

用法：
    uv run python -m scripts.generate_openapi_contract

輸出**穩定排序、格式固定**：keys 排序、縮排固定、結尾換行——否則每次重新
產生都會出現無意義的 diff（例如 dict 迭代順序恰好不同），讓 code review
沒辦法一眼看出這次改動實際異動了契約的哪裡。

這支腳本本身**不判斷**這次改動是不是破壞性的——那是 CI 契約閘門
（`.github/workflows/`）用 `oasdiff` 比對這支腳本的輸出跟已 commit 的
`contracts/openapi.json` 的工作。這裡只負責「產生」，判斷交給別的地方，
兩件事分開才不會互相牽制。
"""
import json
from pathlib import Path

from app.main import app

_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "contracts" / "openapi.json"


def generate() -> str:
    schema = app.openapi()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT_PATH.write_text(generate(), encoding="utf-8")
    print(f"[OK] 契約已寫入 {_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
