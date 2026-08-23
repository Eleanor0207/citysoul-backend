"""把 `content/spirits.yaml` 匯入 `spirits` 與 `brain.characters`。

用法：
    python -m scripts.import_spirits [--dry-run]

冪等：重跑是 UPDATE，不會長出第二列。

## 為什麼一支腳本寫兩張表

`spirits`（身體）與 `brain.characters`（腦袋）是同一件事的兩面：一個靈魂存在，
就同時需要一個可召喚的座標與一個可以掛人格的身分。分成兩支腳本的話，只跑其中
一支的狀態——有座標沒身分，或有身分召喚不到——都不是任何人想要的中間態。

兩張表在不同 schema，但都在同一個交易裡。

## `is_active: false` 是「先不出貨」，不是刪除

YAML 沒寫就是 `true`。寫 `false` 的靈魂照樣進資料庫、照樣被這支腳本管理，只是
召喚不到——史實與人格都留著，要放行只需要把旗標翻回來重跑一次。

刻意不提供刪除路徑：這支腳本只認得 YAML 裡有的東西，「不在清單裡」跟「要刪掉」
從它的角度看是同一件事，而那兩者的後果差很多。真的要移除一個靈魂，是人去下
DELETE，不是靠某一次匯入順手做掉。

## 這支腳本不碰人格

`brain.characters` 只有識別與歸屬，沒有內容。人格走 `import_personas.py`，
而且一律以 `active=false` 進去——建立靈魂跟讓它開口說話是兩件事。

## ⚠️ 座標未經勘查

見 `content/spirits.yaml` 的說明。這些值讓開發與測試跑得起來，不足以出貨。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import yaml
from sqlalchemy import create_engine, text

from app.core.config import settings

SPIRITS_YAML = pathlib.Path(__file__).resolve().parent.parent / "content" / "spirits.yaml"

SUMMON_RADIUS_M = 50
SENSE_RADIUS_M = 150

UPSERT_CHARACTER = text(
    """
    INSERT INTO brain.characters (character_id, landmark_id)
    VALUES (:character_id, :landmark_id)
    ON CONFLICT (character_id) DO UPDATE SET landmark_id = EXCLUDED.landmark_id
    """
)

UPSERT_SPIRIT = text(
    """
    INSERT INTO spirits
        (spirit_id, display_name, character_id, landmark_id, latitude, longitude,
         summon_radius_meters, sense_radius_meters, is_active, safety_gate_enabled)
    VALUES
        (:spirit_id, :display_name, :character_id, :landmark_id, :latitude, :longitude,
         :summon_radius, :sense_radius, :is_active, :safety_gate)
    ON CONFLICT (spirit_id) DO UPDATE SET
        display_name  = EXCLUDED.display_name,
        character_id  = EXCLUDED.character_id,
        landmark_id   = EXCLUDED.landmark_id,
        latitude      = EXCLUDED.latitude,
        longitude     = EXCLUDED.longitude,
        is_active     = EXCLUDED.is_active,
        safety_gate_enabled = EXCLUDED.safety_gate_enabled
    """
)


# 嗓音（0031）。`ON CONFLICT DO UPDATE` 而不是 DO NOTHING：調音是反覆的，
# 每次匯入都要以 YAML 為準覆蓋掉資料庫裡的舊值。
UPSERT_VOICE = text(
    """
    INSERT INTO brain.character_voices
        (character_id, voice_name, speaking_rate, pitch, updated_at)
    VALUES
        (:character_id, :voice_name, :speaking_rate, :pitch, now())
    ON CONFLICT (character_id) DO UPDATE SET
        voice_name    = EXCLUDED.voice_name,
        speaking_rate = EXCLUDED.speaking_rate,
        pitch         = EXCLUDED.pitch,
        updated_at    = now()
    """
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = yaml.safe_load(SPIRITS_YAML.read_text(encoding="utf-8"))
    entries = cfg["spirits"]

    engine = create_engine(settings.database_url)
    errors: list[str] = []
    with engine.connect() as conn:
        known = set(
            conn.execute(text("SELECT landmark_id FROM brain.landmark_souls")).scalars().all()
        )
    for e in entries:
        # spirit_id 就是 landmark_id（2026-08-13 定案，見 citysoul-doc 的
        # landmark/README.md）。不一致代表有人改了其中一邊。
        if e["spirit_id"] not in known:
            errors.append(
                f"{e['spirit_id']}：`brain.landmark_souls` 沒有這一列，外鍵插不進去。"
                " 先跑 scripts.import_landmarks"
            )

        # 嗓音參數的範圍先在這裡擋一次。資料庫也有 CHECK（migration 0031），
        # 但那要等到寫入才報，而 --dry-run 不會寫入——那正是最該看到錯誤的時候。
        voice = e.get("voice")
        if voice is not None:
            if not voice.get("name"):
                errors.append(f"{e['spirit_id']}：voice 少了 name")
            rate = float(voice.get("speaking_rate", 1.0))
            pitch = float(voice.get("pitch", 0.0))
            if not 0.25 <= rate <= 4.0:
                errors.append(f"{e['spirit_id']}：speaking_rate {rate} 超出 0.25–4.0")
            if not -20.0 <= pitch <= 20.0:
                errors.append(f"{e['spirit_id']}：pitch {pitch} 超出 -20.0–20.0")

    if errors:
        for x in errors:
            print(f"✗ {x}")
        return 1

    for e in entries:
        gate = "  [B4 安全閘]" if e.get("safety_gate") else ""
        off = "  [停用]" if not e.get("is_active", True) else ""
        v = e.get("voice")
        voice_note = (
            f"  [voice] {v['name']} rate={v.get('speaking_rate', 1.0)} "
            f"pitch={v.get('pitch', 0.0):+}"
            if v else "  [voice] 未配音"
        )
        print(
            f"  {e['spirit_id']:36s} {e['display_name']:12s} → "
            f"{e['character_id']}{gate}{off}{voice_note}"
        )

    if args.dry_run:
        print("\n--dry-run：沒有寫入資料庫")
        return 0

    with engine.begin() as conn:
        for e in entries:
            conn.execute(
                UPSERT_CHARACTER,
                {"character_id": e["character_id"], "landmark_id": e["spirit_id"]},
            )
            # 沒寫 voice: 的靈魂**不補預設值列**。「還沒配音」與「配成預設值」
            # 是兩件事，後者看起來像有人決定過。
            voice = e.get("voice")
            if voice is not None:
                conn.execute(
                    UPSERT_VOICE,
                    {
                        "character_id": e["character_id"],
                        "voice_name": voice["name"],
                        "speaking_rate": float(voice.get("speaking_rate", 1.0)),
                        "pitch": float(voice.get("pitch", 0.0)),
                    },
                )
            conn.execute(
                UPSERT_SPIRIT,
                {
                    "spirit_id": e["spirit_id"],
                    "display_name": e["display_name"],
                    "character_id": e["character_id"],
                    "landmark_id": e["spirit_id"],
                    "latitude": e["latitude"],
                    "longitude": e["longitude"],
                    "summon_radius": SUMMON_RADIUS_M,
                    "sense_radius": SENSE_RADIUS_M,
                    "is_active": bool(e.get("is_active", True)),
                    "safety_gate": bool(e.get("safety_gate", False)),
                },
            )

    with engine.connect() as conn:
        n_s = conn.execute(text("SELECT count(*) FROM spirits")).scalar()
        n_c = conn.execute(text("SELECT count(*) FROM brain.characters")).scalar()
        n_v = conn.execute(text("SELECT count(*) FROM brain.character_voices")).scalar()
    print(f"\nspirits {n_s} 列，brain.characters {n_c} 列，brain.character_voices {n_v} 列")
    print("⚠️ 座標未經實地勘查，見 content/spirits.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
