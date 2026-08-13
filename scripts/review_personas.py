"""把所有人格草稿印成可讀的樣子，供人工審核。

用法：
    python -m scripts.review_personas            # 只印草稿
    python -m scripts.review_personas --all      # 連生效中的一起印

## 為什麼有這支

審核的實際動作是「一個人把文字讀過一遍」。要讀就得看得到——而 YAML 檔散在
十個檔案裡、資料庫又不好直接查，兩者都讓「讀過一遍」變成一件麻煩事，
麻煩到最後就會有人跳過。

這支腳本讀的是**資料庫**而不是 YAML：審核要對著實際會被注入 prompt 的內容做，
不是對著原始檔。中間如果有任何轉換（例如空白正規化）出了差錯，這裡看得出來。

## 標示重點

`canned_greetings` 用不同的符號標出來，因為那是**唯一不經過 LLM、原樣送到玩家
眼前**的內容——其餘欄位是給模型的指示，措辭差一點還有生成環節可以吸收，
問候語沒有。
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, text

from app.core.config import settings

FIELDS = [
    ("archetype", "核心性格"),
    ("speech_style", "說話風格"),
    ("personality_traits", "性格特質"),
    ("values", "在意的事"),
    ("not_this_character", "不是誰"),
    ("imagination_license", "虛構授權"),
    ("quest_themes", "任務主題"),
    ("tone_override", "語氣調整"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="連生效中的版本一起印")
    args = ap.parse_args()

    engine = create_engine(settings.database_url)
    where = "" if args.all else "WHERE p.active = false"

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""SELECT p.character_id, p.version, p.active, p.reviewed_by,
                           p.archetype, p.speech_style, p.personality_traits, p.values,
                           p.taboos, p.not_this_character, p.imagination_license,
                           p.quest_themes, p.tone_override, s.display_name
                    FROM brain.character_personas p
                    LEFT JOIN spirits s ON s.character_id = p.character_id
                    {where}
                    ORDER BY p.character_id, p.version"""
            )
        ).mappings().all()

        greetings = {}
        for g in conn.execute(
            text(
                """SELECT character_id, version, trigger_phrases, response_text
                   FROM brain.canned_greetings ORDER BY character_id, version"""
            )
        ).all():
            greetings.setdefault((g[0], g[1]), []).append((g[2], g[3]))

    if not rows:
        print("沒有草稿。")
        return 0

    for r in rows:
        state = "生效中" if r["active"] else "草稿"
        print("=" * 78)
        print(f"{r['display_name'] or '（無 spirits 記錄）'}　|　{r['character_id']} v{r['version']}　|　{state}")
        print("=" * 78)

        for key, label in FIELDS:
            v = r[key]
            if not v:
                continue
            if isinstance(v, list):
                v = "、".join(v)
            print(f"\n【{label}】\n{v}")

        print("\n【禁忌】")
        for t in r["taboos"] or []:
            print(f"  - {t}")

        gs = greetings.get((r["character_id"], r["version"]), [])
        if gs:
            print("\n★【預寫問候語】玩家會逐字看到這些，不經過模型")
            for triggers, resp in gs:
                print(f"  觸發：{'／'.join(triggers)}")
                print(f"  回應：{resp}")
        print()

    print("=" * 78)
    print(f"共 {len(rows)} 份。全部上線：")
    print('  python -m scripts.activate_content persona --all --reviewed-by "你的名字"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
