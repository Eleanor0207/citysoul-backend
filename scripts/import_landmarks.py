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

## 基調的審核閘已在 MVP 取消（2026-08-18 決定）

`brain.districts` 有 `active` / `reviewed_by` / `reviewed_at`（0018）。這支腳本
原本永遠把 `active` 寫成 false、把 `reviewed_by` 清成 NULL，要生效得由人跑
`scripts.activate_content` 手動 flip。那道閘門拿掉了：**匯入即生效**。

理由與人格卡那邊相同（見 `import_personas.py`）：審核者只有一個人，而閘門實際
擋住的是自己的產出，不是壞內容。

沒有人看過的基調，`reviewed_by` 記成 `MVP_NO_REVIEW`——讓「這段文字沒有人讀過」
在資料庫裡是一個查得到的值，而不是一個空格。日後恢復審核時，要重看哪些，查這個
字串就知道。

`prompt_builder` 仍然只注入 `active=true` 的基調，那條路徑不變。

## 區級基調寫進 brain.districts（0016）

10 份研究檔各自寫了**自己那一區**的基調。`brain.city_souls` 是一個城市一列、
裝不下這種差異，所以 0016 給 `districts` 加了同名的三個欄位。

同一區有多個地標時會有多份互相競爭的版本（萬華就有三份），由
`content/districts.yaml` 的 `tone_author` 指定誰主寫——沿用 Lead 決策摘要的
「區域基調主寫」指派。沒被指定的那幾份保留在自己的 YAML 裡當素材，不進資料庫。

`landmark_souls.district_id` 也由 `content/districts.yaml` 的 `landmarks` 清單決定。

⚠️ **`boundary` 不在這支腳本的職責內。** 邊界由 `scripts/load_districts.py` 從
GeoJSON 載入，兩支互不覆蓋——這裡的 UPSERT 不碰 `boundary` 那一欄。

## city_souls 由 content/city.yaml 主寫，不從地標檔挑

九份地標檔的 `city_tone` 寫的都是**自己那一區**。從裡面挑一份寫進 city_souls，
臺北就會用那一區的口吻講話——那正是 `0016` 把區級基調拆出去的理由。所以城市層
有自己的 `content/city.yaml`，寫的是九個地標都成立的那一層。

`--city-tone-from <檔名>` 仍然留著，它現在是**覆寫**：指名時改用那一份地標檔的
區級基調，並印出警告。留著是因為第二座城市進來時，可能一時只有地標檔而還沒有
城市層檔案；平常不該用到它。

city_souls 的 `active` 照 YAML 寫，預設 false——`prompt_builder` 還沒讀這一層
（也還沒讀 districts），false 讓「還沒接上」在資料庫裡查得到。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.text_normalize import strip_fold_spaces

CONTENT_DIR = pathlib.Path(__file__).resolve().parent.parent / "content" / "landmarks"
DISTRICTS_YAML = pathlib.Path(__file__).resolve().parent.parent / "content" / "districts.yaml"
CITY_YAML = pathlib.Path(__file__).resolve().parent.parent / "content" / "city.yaml"

# 見 docstring：沒有人讀過的內容，在資料庫裡要是一個查得到的值。
MVP_NO_REVIEW = "MVP_NO_REVIEW"

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
        (district_id, city_id, name, core_tone_descriptors, shared_values, macro_history_summary,
         active, reviewed_by, reviewed_at)
    VALUES (:district_id, :city_id, :name, :core_tone_descriptors, :shared_values, :macro,
            true, :reviewed_by, now())
    ON CONFLICT (district_id) DO UPDATE SET
        city_id               = EXCLUDED.city_id,
        name                  = EXCLUDED.name,
        core_tone_descriptors = EXCLUDED.core_tone_descriptors,
        shared_values         = EXCLUDED.shared_values,
        macro_history_summary = EXCLUDED.macro_history_summary,
        -- MVP 取消人工文字審核（2026-08-18），基調改成匯入即生效。見 docstring。
        active                = true,
        reviewed_by           = EXCLUDED.reviewed_by,
        reviewed_at           = EXCLUDED.reviewed_at
    """
)

UPSERT_CITY = text(
    """
    INSERT INTO brain.city_souls
        (city_id, name, macro_history_summary, core_tone_descriptors, shared_values,
         active, reviewed_by, reviewed_at, updated_at)
    VALUES (:city_id, :name, :macro_history_summary, :core_tone_descriptors, :shared_values,
            :active, :reviewed_by, now(), now())
    ON CONFLICT (city_id) DO UPDATE SET
        name                  = EXCLUDED.name,
        macro_history_summary = EXCLUDED.macro_history_summary,
        core_tone_descriptors = EXCLUDED.core_tone_descriptors,
        shared_values         = EXCLUDED.shared_values,
        active                = EXCLUDED.active,
        reviewed_by           = EXCLUDED.reviewed_by,
        reviewed_at           = now(),
        updated_at            = now()
    """
)


def validate_city(data: dict) -> list[str]:
    """
    城市層的驗證。跟地標的 `validate` 分開，因為要求不一樣：這裡沒有史實表，
    但三個欄位一個都不能是佔位字串——城市層只有一列，缺一欄就是缺三分之一。
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"{CITY_YAML.name} 不是一個 mapping"]

    for key in ("city_id", "name", "macro_history_summary"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{CITY_YAML.name}：`{key}` 缺漏或不是字串")
        elif any(ph in value for ph in PLACEHOLDERS):
            errors.append(f"{CITY_YAML.name}：`{key}` 還是佔位字串")

    for key in ("core_tone_descriptors", "shared_values"):
        value = data.get(key)
        if not isinstance(value, list) or not value:
            errors.append(f"{CITY_YAML.name}：`{key}` 缺漏或不是非空陣列")
            continue
        for item in value:
            if not isinstance(item, str) or not item.strip():
                errors.append(f"{CITY_YAML.name}：`{key}` 有空白項目")
            elif any(ph in item for ph in PLACEHOLDERS):
                errors.append(f"{CITY_YAML.name}：`{key}` 還是佔位字串")

    if not isinstance(data.get("active", False), bool):
        errors.append(f"{CITY_YAML.name}：`active` 不是布林值")

    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--content-dir", default=str(CONTENT_DIR))
    ap.add_argument(
        "--city-tone-from",
        metavar="FILE",
        help="改用某一份地標檔的區級 city_tone 覆寫 brain.city_souls（見 docstring）",
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
        # YAML 折行在中文之間留下的空格，見 app/core/text_normalize.py
        data = strip_fold_spaces(data)
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

    # 見 docstring：城市層由 content/city.yaml 主寫，地標檔的 city_tone 是區級的。
    have_tone = [(p, d) for p, d in loaded if d.get("city_tone")]
    chosen = None
    city: dict | None = None
    if args.city_tone_from:
        wanted = pathlib.Path(args.city_tone_from).name
        chosen = next((x for x in have_tone if x[0].name == wanted), None)
        if chosen is None:
            errors.append(
                f"--city-tone-from {wanted} 找不到，或那一份沒有 city_tone。"
                f" 有 city_tone 的是：{'、'.join(p.name for p, _ in have_tone)}"
            )
        else:
            warns.append(
                f"--city-tone-from：city_souls 改用 {wanted} 的**區級**基調覆寫。"
                f" 平常應該讓 {CITY_YAML.name} 主寫，那一份才是城市高度的內容"
            )
    elif CITY_YAML.exists():
        city = strip_fold_spaces(yaml.safe_load(CITY_YAML.read_text(encoding="utf-8")))
        errors.extend(validate_city(city))
    else:
        warns.append(
            f"沒有 {CITY_YAML.name}，這次不寫 city_souls。"
            " 城市層要有自己的內容，不要從地標檔的區級 city_tone 挑一份頂替"
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
                    "reviewed_by": MVP_NO_REVIEW,
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
                    # 覆寫路徑寫的是區級內容，沒有人以城市層的高度審過它。
                    "active": False,
                    "reviewed_by": MVP_NO_REVIEW,
                },
            )
            print(f"\ncity_souls 的基調由 {path.name} 的區級 city_tone 覆寫（active=false）")
        elif city is not None:
            conn.execute(
                UPSERT_CITY,
                {
                    "city_id": city["city_id"],
                    "name": city["name"],
                    "macro_history_summary": city["macro_history_summary"],
                    "core_tone_descriptors": city["core_tone_descriptors"],
                    "shared_values": city["shared_values"],
                    "active": bool(city.get("active", False)),
                    "reviewed_by": city.get("reviewed_by") or MVP_NO_REVIEW,
                },
            )
            print(
                f"\ncity_souls 由 {CITY_YAML.name} 主寫"
                f"（active={bool(city.get('active', False))}，"
                f"reviewed_by={city.get('reviewed_by') or MVP_NO_REVIEW}）"
            )

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
