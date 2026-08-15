"""把 `content/personas/*.yaml` 匯出成 citysoul-doc/character/ 的 markdown。

用法：
    python -m scripts.export_personas_md [--out <目錄>]

## 方向是單向的

YAML 是正本，markdown 是給人讀的副本。改人格請改 YAML 再重跑這支腳本——
反過來改 markdown 不會有任何效果，匯入器不讀它。

之所以還要有 markdown：人格卡是需要人審核的內容，而審核者不應該被迫在 YAML 的
引號與縮排裡讀一段中文敘述。研究檔（`citysoul-doc/landmark/`）也是同樣的分工，
只是方向相反——那邊是 markdown 為正本，YAML 由 `landmark_md_to_yaml.py` 產生。

## 審核狀態不從這裡看

`reviewed_by` 是 YAML 裡的值，代表**這份草稿檔**的簽名。實際生效與否看資料庫的
`brain.character_personas.active`，那一欄只有人能 flip，且匯入器永遠寫 false。
兩邊可能不一致（例如簽名是直接對資料庫下 UPDATE 的），所以每份輸出都會把這件事
寫在檔頭，不要把 markdown 當成上線狀態的依據。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import yaml

from app.core.text_normalize import strip_fold_spaces

PERSONA_DIR = pathlib.Path(__file__).resolve().parent.parent / "content" / "personas"
DEFAULT_OUT = pathlib.Path(r"F:\Pre_Work_AI\citysoul-doc\character")


def _t(s: str) -> str:
    """YAML 的 `>-` 折行在中文之間會留下一個空格（「南北貨的 行情」）。

    這跟 prompt 組裝時遇到的是同一個問題，用同一個函式處理。
    """
    return strip_fold_spaces(s)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {_t(x)}" for x in items)


def _greetings(items: list[dict]) -> str:
    out = []
    for g in items:
        triggers = "、".join(f"「{t}」" for t in g["trigger_phrases"])
        out.append(f"| {triggers} | {_t(g['response_text'])} |")
    header = "| 觸發語 | 回應 |\n| --- | --- |"
    return header + "\n" + "\n".join(out)


def render(stem: str, d: dict) -> str:
    tone = _t(d.get("tone_override") or "") or "（無，沿用行政區基調）"
    return f"""# {stem}

<!-- 由 citysoul-backend 的 scripts/export_personas_md.py 產生，請勿手改。
     正本是 content/personas/{stem}.yaml。 -->

| 欄位 | 值 |
| --- | --- |
| `character_id` | `{d['character_id']}` |
| `version` | {d['version']} |
| `reviewed_by` | `{d['reviewed_by']}` |

> 上線與否看資料庫的 `brain.character_personas.active`，不看這份文件。
> 這裡的 `reviewed_by` 只是草稿檔上的簽名。

## 原型（archetype）

{_t(d['archetype'])}

## 說話風格（speech_style）

{_t(d['speech_style'])}

## 特質（personality_traits）

{_bullets(d['personality_traits'])}

## 價值觀（values）

{_bullets(d['values'])}

## 禁忌（taboos）

{_bullets(d['taboos'])}

## 不是這個角色（not_this_character）

{_t(d['not_this_character'])}

> 這一段是 CONTEXT.md 的安全下限，敘事審查只能往上加，不能改寫。

## 想像範圍（imagination_license）

{_t(d['imagination_license'])}

> 史實邊界的另一半在 B5 規則與 `brain.landmark_souls` 的史實層，不在人格卡裡。

## 任務主題（quest_themes）

{_bullets(d['quest_themes'])}

## 基調覆寫（tone_override）

{tone}

## 罐頭問候（canned_greetings，B12／AC7.1）

{_greetings(d['canned_greetings'])}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    files = sorted(PERSONA_DIR.glob("*.yaml"))
    if not files:
        print(f"✗ {PERSONA_DIR} 沒有任何 yaml")
        return 1

    for p in files:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        target = args.out / f"{p.stem}.md"
        target.write_text(render(p.stem, d), encoding="utf-8")
        print(f"  {target}")

    print(f"\n{len(files)} 份")
    return 0


if __name__ == "__main__":
    sys.exit(main())
