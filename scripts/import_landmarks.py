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

## city_tone 預設不匯入，要匯入必須指名來源

10 份研究檔各自寫了**自己那一區**的基調（Lead 決策摘要稱之為「區域基調主寫」，
萬華、大同、士林……各一份），但 `brain.city_souls` 是**一個城市一列**，沒有分區
維度；`brain.districts` 也沒有基調欄位。**區級基調目前在 schema 裡沒有地方放。**

這不是這支腳本能決定的事——要嘛挑一份當全臺北的基調，要嘛給 `districts` 加欄位。
所以預設**完全不碰 `city_souls`**，要寫必須用 `--city-tone-from <檔名>` 指名，
把「誰代表臺北」這個決定留在指令上，而不是藏在檔名排序裡。

地標本身的匯入不受這件事阻擋：`landmark_souls` 跟 `city_souls` 是兩張表。
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

    # 見 docstring：city_tone 預設不匯入，要匯入必須用 --city-tone-from 指名。
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
        for _, data in loaded:
            conn.execute(
                UPSERT_LANDMARK,
                {
                    "landmark_id": data["landmark_id"],
                    "city_id": data["city_id"],
                    "district_id": data.get("district_id"),
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
