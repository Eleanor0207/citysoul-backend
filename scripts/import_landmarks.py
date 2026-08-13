"""把 `content/landmarks/*.yaml` 匯入 `brain.landmark_souls`。

用法：
    python -m scripts.import_landmarks            # 驗證後寫入
    python -m scripts.import_landmarks --dry-run  # 只驗證與列出差異，不寫

冪等：同一個 `landmark_id` 重跑是 UPDATE，不會長出第二列。

## 先全部驗完，才開始寫

十份檔案是一批。第七份有問題時就中止，代表前六份已經進了資料庫而後四份沒有
——那個狀態沒有人想收拾，而且從資料庫外面看不出來它是半套的。所以驗證跑完
才開一個交易，任何一份不合格就整批不寫。

## 史實層沒有審核欄位

`landmark_souls` 不像 `character_personas` 有 `active` / `reviewed_by`，寫進去就
生效。這裡的把關只能是**匯入前**的：YAML 由 `landmark_md_to_yaml.py` 從研究檔
產生，而研究檔已經過 Lead 覆核。這支腳本會擋掉明顯未完成的內容（佔位字串、空的
史實表），但它擋不掉「寫錯的史實」——那是人的責任。

## 🔒 基調有審核閘，這支腳本只會把它關掉

`brain.districts` 有 `active` / `reviewed_by` / `reviewed_at`（0018），語意與人格
卡一致：**沒有任何程式路徑會把 `active` 設成 true**，只有人工審核流程能 flip。
`prompt_builder` 只注入 `active=true` 的基調。

而且**重新匯入會把已審核的列退回未審核**。內容換了、審核狀態留著，等於讓上一次
的簽名替這一次的文字背書——那比一開始就沒有審核更糟，因為它看起來是有審核的。

## 區級基調寫進 brain.districts（0016）

10 份研究檔各自寫了**自己那一區**的基調。`brain.city_souls` 是一個城市一列、
裝不下這種差異，所以 0016 給 `districts` 加了同名的三個欄位。

同一區有多個地標時會有多份互相競爭的版本（萬華就有三份），由
`content/districts.yaml` 的 `tone_author` 指定誰主寫——沿用 Lead 決策摘要的
「區域基調主寫」指派。沒被指定的那幾份保留在自己的 YAML 裡當素材，不進資料庫。

`landmark_souls.district_id` 也由 `content/districts.yaml` 的 `landmarks` 清單決定。

⚠️ **`boundary` 不在這支腳本的職責內。** 邊界由 `scripts/load_districts.py` 從
GeoJSON 載入，兩支互不覆蓋——這裡的 UPSERT 不碰 `boundary` 那一欄。

## city_souls 一律不碰

城市層目前沒有人在寫（`macro_history_summary` 還是 PENDING 佔位）。要寫必須用
`--city-tone-from <檔名>` 明確指名，把「誰代表臺北」的決定留在指令上。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml
from sqlalchemy import create_engine, text

from app.core.config import settings

CONTENT_DIR = pathlib.Path(__file__).resolve().parent.parent / "content" / "landmarks"
DISTRICTS_YAML = pathlib.Path(__file__).resolve().parent.parent / "content" / "districts.yaml"

# 研究檔還沒寫完時留下的記號。這些不該進資料庫。
PLACEHOLDERS = ("PENDING_NARRATIVE_REVIEW", "PENDING_HUMAN_REVIEW", "待定", "TODO")

REQUIRED = ("landmark_id", "name", "city_id", "founding_facts")


def validate(path: pathlib.Path, data: dict) -> list[str]:
    """回傳這一份的錯誤清單。空清單代表通過。"""
    errors: list[str] = []
    where = path.name

    for key in REQUIRED:
        if not data.get(key):
            errors.append(f"{where}：缺少必要欄位 `{key}`")

    if data.get("landmark_id") and data["landmark_id"] != path.stem:
        errors.append(
            f"{where}：`landmark_id`（{data['landmark_id']}）與檔名（{path.stem}）不一致。"
            " 檔名就是 spirit_id，兩者必須相同"
        )

    for i, fact in enumerate(data.get("founding_facts") or []):
        if not fact.get("detail"):
            errors.append(f"{where}：founding_facts[{i}] 沒有 detail")
        if fact.get("confidence") not in ("official", "mainstream"):
            errors.append(
                f"{where}：founding_facts[{i}] 的 confidence 是"
                f" {fact.get('confidence')!r}，只有 official／mainstream 能進史實層"
            )

    blob = yaml.safe_dump(data, allow_unicode=True)
    for token in PLACEHOLDERS:
        if token in blob:
            errors.append(f"{where}：內容含未完成記號 {token!r}")

    return errors


def warnings_for(path: pathlib.Path, data: dict) -> list[str]:
    """不擋匯入，但值得說一聲的事。"""
    warn = []
    if not data.get("cultural_significance"):
        warn.append(f"{path.name}：沒有 cultural_significance")
    if not data.get("key_events"):
        warn.append(f"{path.name}：沒有 key_events")
    for i, fact in enumerate(data.get("founding_facts") or []):
        # SOP 建議 detail ≤ 50 字。超過不擋——史實層整段注入 prompt，長一點
        # 只是佔 token，不會壞掉。
        if len(fact.get("detail", "")) > 60:
            warn.append(f"{path.name}：founding_facts[{i}] 的 detail 超過 60 字")
    return warn


UPSERT_LANDMARK = text(
    """
    INSERT INTO brain.landmark_souls
        (landmark_id, city_id, district_id, name, founding_facts, key_events,
         cultural_significance, common_misconceptions, updated_at)
    VALUES
        (:landmark_id, :city_id, :district_id, :name,
         CAST(:founding_facts AS jsonb), CAST(:key_events AS jsonb),
         :cultural_significance, CAST(:common_misconceptions AS jsonb), now())
    ON CONFLICT (landmark_id) DO UPDATE SET
        city_id               = EXCLUDED.city_id,
        name                  = EXCLUDED.name,
        founding_facts        = EXCLUDED.founding_facts,
        key_events            = EXCLUDED.key_events,
        cultural_significance = EXCLUDED.cultural_significance,
        common_misconceptions = EXCLUDED.common_misconceptions,
        updated_at            = now(),
        -- district_id 只在 YAML 有給值時才覆蓋。研究檔目前不寫這一欄，
        -- 而龍山寺的 'wanhua' 是載入邊界時設的——EXCLUDED 直接蓋會把它清成 NULL。
        district_id           = COALESCE(EXCLUDED.district_id, brain.landmark_souls.district_id)
    """
)

# ⚠️ 沒有 boundary 這一欄，是刻意的：邊界由 load_districts.py 負責，這裡蓋過去
# 會把已經載好的萬華多邊形清成 NULL，而圍欄判斷失效不會有任何錯誤訊息。
UPSERT_DISTRICT = text(
    """
    INSERT INTO brain.districts
        (district_id, city_id, name, core_tone_descriptors, shared_values, macro_history_summary)
    VALUES (:district_id, :city_id, :name, :core_tone_descriptors, :shared_values, :macro)
    ON CONFLICT (district_id) DO UPDATE SET
        city_id               = EXCLUDED.city_id,
        name                  = EXCLUDED.name,
        core_tone_descriptors = EXCLUDED.core_tone_descriptors,
        shared_values         = EXCLUDED.shared_values,
        macro_history_summary = EXCLUDED.macro_history_summary,
        -- 🔒 改了基調文字就退回未審核。內容變了而審核狀態沒變，等於讓上一次的
        -- 簽名替這一次的文字背書——那比一開始就沒有審核更糟。
        active                = false,
        reviewed_by           = NULL,
        reviewed_at           = NULL
    """
)

UPSERT_CITY = text(
    """
    INSERT INTO brain.city_souls
        (city_id, name, macro_history_summary, core_tone_descriptors, shared_values, updated_at)
    VALUES (:city_id, :name, :macro_history_summary, :core_tone_descriptors, :shared_values, now())
    ON CONFLICT (city_id) DO UPDATE SET
        macro_history_summary = EXCLUDED.macro_history_summary,
        core_tone_descriptors = EXCLUDED.core_tone_descriptors,
        shared_values         = EXCLUDED.shared_values,
        updated_at            = now()
    """
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--content-dir", default=str(CONTENT_DIR))
    ap.add_argument(
        "--city-tone-from",
        metavar="FILE",
        help="指名由哪一份 YAML 的 city_tone 寫入 brain.city_souls（見 docstring）",
    )
    args = ap.parse_args()

    content_dir = pathlib.Path(args.content_dir)
    files = sorted(content_dir.glob("*.yaml"))
    if not files:
        raise SystemExit(f"{content_dir} 沒有 .yaml")

    loaded: list[tuple[pathlib.Path, dict]] = []
    errors: list[str] = []
    warns: list[str] = []
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        loaded.append((path, data))
        errors.extend(validate(path, data))
        warns.extend(warnings_for(path, data))

    # 行政區指派（content/districts.yaml）
    districts_cfg = yaml.safe_load(DISTRICTS_YAML.read_text(encoding="utf-8"))
    by_id = {d["landmark_id"]: d for _, d in loaded}
    landmark_district: dict[str, str] = {}
    for d in districts_cfg["districts"]:
        for lm in d["landmarks"]:
            if lm not in by_id:
                errors.append(
                    f"districts.yaml：{d['district_id']} 列出的 {lm} 沒有對應的 YAML"
                )
            if lm in landmark_district:
                errors.append(f"districts.yaml：{lm} 被指派到兩個行政區")
            landmark_district[lm] = d["district_id"]
        author = d["tone_author"]
        if author not in d["landmarks"]:
            errors.append(
                f"districts.yaml：{d['district_id']} 的 tone_author（{author}）"
                " 不在自己的 landmarks 清單裡"
            )
        elif author in by_id and not by_id[author].get("city_tone"):
            errors.append(f"districts.yaml：{author} 被指定主寫 {d['district_id']} 基調，但它沒有 city_tone")
    for lm in by_id:
        if lm not in landmark_district:
            warns.append(f"{lm} 沒有被指派到任何行政區，district_id 會留白")

    # 見 docstring：city_souls 一律不碰，除非 --city-tone-from 指名。
    have_tone = [(p, d) for p, d in loaded if d.get("city_tone")]
    chosen = None
    if args.city_tone_from:
        wanted = pathlib.Path(args.city_tone_from).name
        chosen = next((x for x in have_tone if x[0].name == wanted), None)
        if chosen is None:
            errors.append(
                f"--city-tone-from {wanted} 找不到，或那一份沒有 city_tone。"
                f" 有 city_tone 的是：{'、'.join(p.name for p, _ in have_tone)}"
            )
    elif have_tone:
        warns.append(
            f"{len(have_tone)} 份 YAML 帶 city_tone，但那是**各行政區**的基調，"
            " 而 brain.city_souls 是一個城市一列。這次不寫 city_souls；"
            " 要寫請用 --city-tone-from 指名一份"
        )

    for w in warns:
        print(f"⚠️  {w}")
    if errors:
        print()
        for e in errors:
            print(f"✗ {e}")
        print(f"\n{len(errors)} 個問題，整批不匯入。")
        return 1

    print(f"\n{len(loaded)} 份 YAML 全部通過驗證")
    for path, data in loaded:
        print(
            f"  {data['landmark_id']:34s} {data['name']:12s}"
            f" facts={len(data.get('founding_facts') or [])}"
            f" events={len(data.get('key_events') or [])}"
        )

    if args.dry_run:
        print("\n--dry-run：沒有寫入資料庫")
        return 0

    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        # 先寫行政區：landmark_souls.district_id 是指向它的外鍵。
        for d in districts_cfg["districts"]:
            tone = by_id[d["tone_author"]]["city_tone"]
            conn.execute(
                UPSERT_DISTRICT,
                {
                    "district_id": d["district_id"],
                    "city_id": districts_cfg["city_id"],
                    "name": d["name"],
                    "core_tone_descriptors": tone.get("core_tone_descriptors"),
                    "shared_values": tone.get("shared_values"),
                    "macro": tone.get("macro_history_summary"),
                },
            )

        for _, data in loaded:
            conn.execute(
                UPSERT_LANDMARK,
                {
                    "landmark_id": data["landmark_id"],
                    "city_id": data["city_id"],
                    "district_id": landmark_district.get(data["landmark_id"]),
                    "name": data["name"],
                    "founding_facts": json.dumps(
                        data["founding_facts"], ensure_ascii=False
                    ),
                    "key_events": json.dumps(
                        data.get("key_events") or [], ensure_ascii=False
                    ),
                    "cultural_significance": data.get("cultural_significance"),
                    "common_misconceptions": (
                        json.dumps(data["common_misconceptions"], ensure_ascii=False)
                        if data.get("common_misconceptions")
                        else None
                    ),
                },
            )

        if chosen is not None:
            path, data = chosen
            tone = data["city_tone"]
            conn.execute(
                UPSERT_CITY,
                {
                    "city_id": data["city_id"],
                    "name": "臺北",
                    "macro_history_summary": tone.get("macro_history_summary"),
                    "core_tone_descriptors": tone.get("core_tone_descriptors"),
                    "shared_values": tone.get("shared_values"),
                },
            )
            print(f"\ncity_souls 的基調由 {path.name} 主寫")

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """SELECT landmark_id, name,
                          jsonb_array_length(founding_facts),
                          coalesce(jsonb_array_length(key_events), 0)
                   FROM brain.landmark_souls ORDER BY landmark_id"""
            )
        ).all()
    print(f"\nbrain.landmark_souls 現有 {len(rows)} 列")
    return 0


if __name__ == "__main__":
    sys.exit(main())
