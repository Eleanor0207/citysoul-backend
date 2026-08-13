"""把 `citysoul-doc/landmark/*.md` 的研究記錄轉成 `content/landmarks/*.yaml`。

用法：
    python -m scripts.landmark_md_to_yaml [--doc-repo ../citysoul-doc] [--check]

`--check` 只印出解析結果與缺漏，不寫檔。

## 為什麼要有中間的 YAML，不直接從 markdown 匯入

markdown 是**寫給人看的**：章節措辭會變（「建廟史實」／「建館沿革」／「街區形成」）、
會多出治理提醒區塊、表格欄位順序也不保證。直接讓匯入器吃 markdown，等於讓資料
庫的內容取決於某個人怎麼下標題——而且解析失敗最可能的表現不是報錯，是**靜默少
匯了一列史實**。

YAML 是機器讀的中間層：結構固定、可以進版控、可以被 diff。這支轉檔器負責跨過
那道不穩定的邊界，並且**把它解析不出來的東西大聲講出來**，而不是預設略過。

## 解析錨點是欄位名，不是章節編號

每個章節標題都帶「（對應 `founding_facts`）」這種標記。用它當錨點，章節編號改了、
措辭改了都不影響。找不到某個欄位的章節時會回報，不會安靜地當成空值。

## confidence 決定進不進史實層

`disputed` 的條目**不寫進 `founding_facts` / `key_events`**，改放 `disputed` 區塊
供 Lead 判斷（SOP §2 的分級）。這條規則寫在轉檔器裡而不是匯入器裡，是因為它是
研究方法的一部分，在 YAML 階段就該看得出來哪些被排除了。
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

import yaml

# 研究檔的檔名就是 spirit_id（2026-08-13 定案，見 citysoul-doc 的 landmark/README.md）。
# 例外：檔名為研究記錄用途、不是地標本身的那幾份。
SKIP = {"README", "landmark_research_sop", "lead_decisions_v3"}

# 章節標題裡的「對應 `欄位`」標記 → YAML key
FIELD_SECTIONS = {
    "founding_facts": "founding_facts",
    "key_events": "key_events",
    "cultural_significance": "cultural_significance",
    "common_misconceptions": "common_misconceptions",
    "brain.city_souls": "city_tone",
}

CONFIDENCE_OK = {"official", "mainstream"}


def split_sections(text: str) -> dict[str, str]:
    """`## ` 標題切段，回傳 {對應欄位: 該段內容}。"""
    out: dict[str, str] = {}
    parts = re.split(r"^## ", text, flags=re.M)
    for part in parts[1:]:
        heading, _, body = part.partition("\n")
        for marker, key in FIELD_SECTIONS.items():
            if f"`{marker}`" in heading:
                out[key] = body
                break
    return out


def parse_table(body: str) -> list[dict[str, str]]:
    """markdown 表格 → list of dict，用表頭當 key。空白列與分隔列跳過。"""
    rows = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("|")]
    if len(rows) < 2:
        return []

    def cells(line: str) -> list[str]:
        return [c.strip().strip("`") for c in line.strip("|").split("|")]

    header = cells(rows[0])
    out = []
    for line in rows[1:]:
        if set(line.replace("|", "").strip()) <= {"-", ":", " "}:
            continue
        values = cells(line)
        if len(values) != len(header):
            continue
        row = dict(zip(header, values))
        if not any(row.values()):  # 整列空白的範本列
            continue
        out.append(row)
    return out


def parse_facts(body: str) -> tuple[list[dict], list[dict]]:
    """史實表 → (可進資料庫的, 因 confidence 被排除的)。"""
    keep, dropped = [], []
    for row in parse_table(body):
        year = row.get("年份", "")
        event = row.get("事件", "")
        detail = row.get("內容（≤50字）") or row.get("內容") or ""
        if not (year or event or detail):
            continue
        conf = (row.get("可信度") or "").strip()
        item = {
            "year": year,
            "event": event,
            "detail": detail,
            "source": row.get("來源", ""),
            "confidence": conf,
        }
        (keep if conf in CONFIDENCE_OK else dropped).append(item)
    return keep, dropped


def parse_blockquote(body: str) -> str:
    """取段落裡的引言（`>` 開頭），多行接成一段。"""
    lines = [
        ln.strip().lstrip(">").strip()
        for ln in body.splitlines()
        if ln.strip().startswith(">") or ln.strip().startswith("> ")
    ]
    return " ".join(x for x in lines if x).strip()


def parse_city_tone(body: str) -> tuple[dict, list[str]]:
    """
    城市基調素材：兩個陣列 + 一段敘述。回傳 (資料, 警告)。

    ⚠️ 這一段的解析踩過兩次，兩次都是**靜默抓不到值**（沒有例外、沒有警告，
    YAML 就是少了一個 key），所以現在的寫法是刻意保守的。

    兩個實際存在的格式差異：

        - `core_tone_descriptors`（關鍵詞陣列，如：市井、信仰）：`["市井", ...]`
        - `core_tone_descriptors`（關鍵詞陣列，如：次文化、混血）：
          `["次文化聚落", ...]`

    第一個陷阱是括號裡的說明**自帶一個全形冒號**，所以「切到第一個冒號」會停在
    括號中間。第二個陷阱是值可能換到下一行，所以「只看同一行」也不行。

    做法：先用標籤位置把整段切成幾塊，再在該塊裡找第一個 `[...]`。
    兩種排版都能過，而且值不在自己那一塊裡也不會誤抓到隔壁欄位的值。
    """
    tone: dict = {}
    warn: list[str] = []

    labels = ("core_tone_descriptors", "shared_values")
    positions = {}
    for key in labels:
        idx = body.find(f"`{key}`")
        if idx == -1:
            warn.append(f"城市基調素材缺 `{key}` 這一行")
        else:
            positions[key] = idx

    for key, idx in positions.items():
        # 這一塊的結尾＝下一個標籤的開頭（或整段結尾）
        later = [i for k, i in positions.items() if i > idx]
        stop = min(later) if later else len(body)
        chunk = body[idx:stop]
        m = re.search(r"\[[^\]]*\]", chunk)
        if not m:
            warn.append(f"`{key}` 沒有填值（研究檔留空）")
            continue
        try:
            value = yaml.safe_load(m.group(0))
        except yaml.YAMLError:
            warn.append(f"`{key}` 的值不是合法陣列：{m.group(0)[:40]}")
            continue
        if not isinstance(value, list) or not value:
            warn.append(f"`{key}` 解析出來不是非空陣列：{value!r}")
            continue
        tone[key] = value

    summary = parse_blockquote(body)
    if summary:
        tone["macro_history_summary"] = summary
    else:
        warn.append("城市基調素材沒有 macro_history_summary 草稿")
    return tone, warn


def parse_misconceptions(body: str) -> list[dict]:
    out = []
    for row in parse_table(body):
        if not row.get("misconception"):
            continue
        out.append(
            {
                "misconception": row["misconception"],
                "correction": row.get("correction", ""),
                "say_instead": row.get("say_instead", ""),
                "source": row.get("source", ""),
            }
        )
    return out


def convert(path: pathlib.Path) -> tuple[dict, list[str]]:
    """回傳 (yaml 資料, 警告清單)。警告不會中止轉檔，但會全部印出來。"""
    text = path.read_text(encoding="utf-8")
    sections = split_sections(text)
    warn: list[str] = []

    spirit_id = path.stem
    name_match = re.search(r"^#\s*(.+?)\s*史實研究記錄範本", text, flags=re.M)
    name = name_match.group(1).strip() if name_match else spirit_id
    # 「臺北當代藝術館（MOCA）」這種括號註記不進資料庫的顯示名稱。
    name = re.sub(r"（[^）]*）$", "", name).strip()

    data: dict = {"landmark_id": spirit_id, "name": name, "city_id": "taipei"}

    for key, label in [("founding_facts", "史實"), ("key_events", "其他重大事件")]:
        if key not in sections:
            warn.append(f"找不到「{label}」章節（對應 `{key}`）")
            continue
        keep, dropped = parse_facts(sections[key])
        if not keep:
            warn.append(f"{label}表沒有任何 confidence 為 official／mainstream 的列")
        data[key] = keep
        if dropped:
            data.setdefault("excluded_by_confidence", []).extend(dropped)
            warn.append(
                f"{label}有 {len(dropped)} 列因 confidence 非 official／mainstream 未收錄"
            )

    if "cultural_significance" in sections:
        cs = parse_blockquote(sections["cultural_significance"])
        if cs:
            data["cultural_significance"] = cs
        else:
            warn.append("文化意義章節的草稿是空的")
    else:
        warn.append("找不到「文化意義」章節")

    if "common_misconceptions" in sections:
        mis = parse_misconceptions(sections["common_misconceptions"])
        if mis:
            data["common_misconceptions"] = mis

    if "city_tone" in sections:
        tone, tone_warn = parse_city_tone(sections["city_tone"])
        warn.extend(tone_warn)
        if tone:
            data["city_tone"] = tone
    else:
        warn.append("找不到「城市基調素材」章節")

    return data, warn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc-repo", default="../citysoul-doc")
    ap.add_argument("--out", default="content/landmarks")
    ap.add_argument("--check", action="store_true", help="只檢查，不寫檔")
    args = ap.parse_args()

    src_dir = pathlib.Path(args.doc_repo).expanduser().resolve() / "landmark"
    if not src_dir.is_dir():
        raise SystemExit(f"找不到 {src_dir}（用 --doc-repo 指定 citysoul-doc 的位置）")

    out_dir = pathlib.Path(args.out)
    if not args.check:
        out_dir.mkdir(parents=True, exist_ok=True)

    total_warn = 0
    for path in sorted(src_dir.glob("*.md")):
        if path.stem in SKIP:
            continue
        data, warn = convert(path)
        facts = len(data.get("founding_facts", []))
        events = len(data.get("key_events", []))
        print(f"{path.stem:34s} founding_facts={facts:<3d} key_events={events:<3d}", end="")
        print(f" misconceptions={len(data.get('common_misconceptions', []))}")
        for w in warn:
            print(f"    ⚠️  {w}")
            total_warn += 1
        if not args.check:
            dst = out_dir / f"{path.stem}.yaml"
            with dst.open("w", encoding="utf-8", newline="\n") as f:
                f.write(f"# 由 scripts/landmark_md_to_yaml.py 從 citysoul-doc 的\n")
                f.write(f"# landmark/{path.name} 產生。要改內容請改那份，不要直接改這裡。\n")
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, width=100)

    print(f"\n警告共 {total_warn} 則")
    return 0


if __name__ == "__main__":
    sys.exit(main())
