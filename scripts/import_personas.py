"""把 `content/personas/*.yaml` 的人格卡匯入 `brain.character_personas`。

用法：
    python -m scripts.import_personas --dry-run
    python -m scripts.import_personas

## MVP 取消了人工文字審核（2026-08-18 決定）

這支腳本原本永遠把 `active` 寫成 `false`，要上線得由人跑 `scripts.activate_content`
手動 flip。那道閘門現在拿掉了：**匯入即上線**。

理由是實測出來的：三份寫好的人格草稿（龍山寺 v4、剝皮寮 v2、西門紅樓 v2）在那道
閘門後面卡了好幾天，而審核者只有一個人。閘門擋住的不是壞內容，是自己的產出。

`scripts/activate_content.py` 留著，它仍然是唯一能把**舊版本**重新扶正的工具。

## 每個角色只有一個 active，所以要先關再開

`uq_character_personas_active` 是偏索引，保證同一個 `character_id` 至多一列
`active = true`。所以匯入 v4 而 v3 還活著會直接違反約束。

順序寫死成「同一個交易內先 deactivate 該角色的其他版本，再 insert 啟用的新版本」。
拆成兩個交易的話，中間那一瞬間該角色沒有任何 active 版本，而正在對話的玩家會拿到
`character_personas` 查不到人格的結果——那不會報錯，只會讓靈魂突然變成沒有性格。

## append 新版本，永不就地覆寫

人格是有版本的（主鍵 `(character_id, version)`）。改人格＝寫一筆新 version，
舊版留著。取消人工審核之後這件事更重要，不是更不重要：現在沒有人在 flip 的時候
看過內容，版本歷史是唯一能回答「這句話是哪一版寫進去的」的東西。

所以同一個 `(character_id, version)` 已存在時，這支腳本**跳過**它，不更新也不
報錯。要改內容就換一個 version 號碼。

跳過而不是報錯，是因為九份人格是一批：其中一份已經匯入過就讓整批失敗的話，
每加一個新地標都要先手動挑掉已完成的檔案。「不覆寫」的保證仍然成立——被跳過的
那一份在資料庫裡一個字都不會變。

## taboos 只能往上加

宗教場域的禁忌是安全下限（CONTEXT.md 與 SDD §12.2），敘事審查只能疊加。新版本
若少了前一版的任何一條，匯入器直接拒絕整批。

**這道檢查刻意不隨人工審核一起拿掉。** 它是機器比對，不花任何人的時間，而它擋的
正好是取消人工審核之後沒有人在看的東西——「順手潤稿」時被弄掉一條下限，弄掉之後
看起來完全正常。

比對對象是「版本號小於這一版的最高版本」，不是全表最高版。重新匯入一份舊版本時，
拿更新的版本當基準會得出無意義的結果。

## reviewed_by 仍然是 NOT NULL

草稿沒填、或還留著 `PENDING_HUMAN_REVIEW` 的，一律記成 `MVP_NO_REVIEW`。

不把欄位改成可空，是為了讓「這份內容沒有人看過」在資料庫裡是一個看得見的值，
而不是一個空格。日後恢復審核時，這一批要重看哪些，查這個字串就知道。

## canned_greetings 跟著版本走

外鍵指向 `(character_id, version)`。預寫台詞是**唯一不經過 LLM、原樣送到玩家
眼前的內容**，改台詞就是改人格內容，要走同一條「換版本號」的路。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import yaml
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.text_normalize import strip_fold_spaces

PERSONA_DIR = pathlib.Path(__file__).resolve().parent.parent / "content" / "personas"

REQUIRED = ("character_id", "version", "archetype", "speech_style")

# 見 docstring：沒有人看過的內容，在資料庫裡要是一個看得見的值。
MVP_NO_REVIEW = "MVP_NO_REVIEW"
PENDING = "PENDING_HUMAN_REVIEW"


def validate(path: pathlib.Path, data: dict, conn) -> list[str]:
    errors: list[str] = []
    where = path.name

    for key in REQUIRED:
        if not data.get(key):
            errors.append(f"{where}：缺少必要欄位 `{key}`")

    if not errors and data.get("character_id"):
        exists = conn.execute(
            text(
                """SELECT 1 FROM brain.character_personas
                   WHERE character_id = :c AND version = :v"""
            ),
            {"c": data["character_id"], "v": data["version"]},
        ).scalar()
        if exists:
            # 見 docstring：跳過，不是錯誤。回傳特殊標記讓呼叫端排除這一份。
            return ["__SKIP__"]

        # taboos 必須是前一版的超集。比對對象是版本號小於這一版的最高版本——
        # 拿全表最高版當基準的話，重匯一份舊版本會被更新的版本擋下來。
        prev = conn.execute(
            text(
                """SELECT version, taboos FROM brain.character_personas
                   WHERE character_id = :c AND version < :v
                   ORDER BY version DESC LIMIT 1"""
            ),
            {"c": data["character_id"], "v": data["version"]},
        ).one_or_none()
        if prev is not None:
            missing = set(prev[1] or []) - set(data.get("taboos") or [])
            if missing:
                errors.append(
                    f"{where}：taboos 少了 v{prev[0]} 就有的條目，只能往上加不能移除——"
                    + "；".join(sorted(missing))
                )

        character_exists = conn.execute(
            text("SELECT 1 FROM brain.characters WHERE character_id = :c"),
            {"c": data["character_id"]},
        ).scalar()
        if not character_exists:
            errors.append(
                f"{where}：`brain.characters` 沒有 {data['character_id']}，外鍵插不進去"
            )

    for i, g in enumerate(data.get("canned_greetings") or []):
        if not g.get("response_text"):
            errors.append(f"{where}：canned_greetings[{i}] 沒有 response_text")
        if not g.get("trigger_phrases"):
            errors.append(f"{where}：canned_greetings[{i}] 沒有 trigger_phrases")

    return errors


# 見 docstring：這兩句的先後順序是偏索引要求的，不能對調也不能拆成兩個交易。
DEACTIVATE_OTHER_VERSIONS = text(
    """
    UPDATE brain.character_personas
    SET active = false
    WHERE character_id = :character_id AND active = true
    """
)

INSERT_PERSONA = text(
    """
    INSERT INTO brain.character_personas
        (character_id, version, archetype, speech_style, personality_traits, values,
         taboos, not_this_character, imagination_license, quest_themes, tone_override,
         daily_event_fallback, quota_fallback, llm_failure_fallback, taboo_redirect_style,
         guided_question_fallback, reviewed_by, reviewed_at, active)
    VALUES
        (:character_id, :version, :archetype, :speech_style, :personality_traits, :values,
         :taboos, :not_this_character, :imagination_license, :quest_themes, :tone_override,
         :daily_event_fallback, :quota_fallback, :llm_failure_fallback, :taboo_redirect_style,
         :guided_question_fallback, :reviewed_by, now(), true)
    """
)

INSERT_GREETING = text(
    """
    INSERT INTO brain.canned_greetings (character_id, version, trigger_phrases, response_text)
    VALUES (:character_id, :version, :trigger_phrases, :response_text)
    """
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--persona-dir", default=str(PERSONA_DIR))
    args = ap.parse_args()

    files = sorted(pathlib.Path(args.persona_dir).glob("*.yaml"))
    if not files:
        raise SystemExit(f"{args.persona_dir} 沒有 .yaml")

    engine = create_engine(settings.database_url)
    loaded = []
    skipped: list[str] = []
    errors: list[str] = []
    with engine.connect() as conn:
        for path in files:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            # YAML 折行在中文之間留下的空格，見 app/core/text_normalize.py
            data = strip_fold_spaces(data)
            if not data.get("reviewed_by") or data["reviewed_by"] == PENDING:
                data["reviewed_by"] = MVP_NO_REVIEW
            problems = validate(path, data, conn)
            if problems == ["__SKIP__"]:
                skipped.append(f"{data['character_id']} v{data['version']}")
                continue
            loaded.append((path, data))
            errors.extend(problems)

    for name in skipped:
        print(f"－ {name} 已存在，跳過")

    if errors:
        for e in errors:
            print(f"✗ {e}")
        print(f"\n{len(errors)} 個問題，整批不匯入。")
        return 1

    for path, d in loaded:
        print(
            f"{d['character_id']} v{d['version']}"
            f"  taboos={len(d.get('taboos') or [])}"
            f"  greetings={len(d.get('canned_greetings') or [])}"
            f"  reviewed_by={d['reviewed_by']}"
        )

    if args.dry_run:
        print("\n--dry-run：沒有寫入資料庫")
        return 0

    with engine.begin() as conn:
        for _, d in loaded:
            conn.execute(DEACTIVATE_OTHER_VERSIONS, {"character_id": d["character_id"]})
            conn.execute(
                INSERT_PERSONA,
                {
                    "character_id": d["character_id"],
                    "version": d["version"],
                    "archetype": d["archetype"],
                    "speech_style": d["speech_style"],
                    "personality_traits": d.get("personality_traits"),
                    "values": d.get("values"),
                    "taboos": d.get("taboos"),
                    "not_this_character": d.get("not_this_character"),
                    "imagination_license": d.get("imagination_license"),
                    "quest_themes": d.get("quest_themes"),
                    "guided_question_fallback": d.get("guided_question_fallback"),
                    "tone_override": d.get("tone_override"),
                    # Optional by design: content authors add this only after
                    # writing and reviewing a player-visible fallback line.
                    "daily_event_fallback": d.get("daily_event_fallback"),
                    # backend#48：一樣是選填。三者都是 NULL 代表尚未填寫，
                    # 呼叫端退回通用保底句，不是這支腳本要擋的錯誤。
                    "quota_fallback": d.get("quota_fallback"),
                    "llm_failure_fallback": d.get("llm_failure_fallback"),
                    "taboo_redirect_style": d.get("taboo_redirect_style"),
                    "reviewed_by": d["reviewed_by"],
                },
            )
            for g in d.get("canned_greetings") or []:
                conn.execute(
                    INSERT_GREETING,
                    {
                        "character_id": d["character_id"],
                        "version": d["version"],
                        "trigger_phrases": g["trigger_phrases"],
                        "response_text": g["response_text"],
                    },
                )

    # 匯入後回讀一次。這支腳本現在會直接改變玩家看到的東西，而「哪一版是生效的」
    # 是它唯一無法從輸入檔推得的結果——同一個角色有多份 YAML 時尤其如此。
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """SELECT character_id, version, active, reviewed_by,
                          cardinality(taboos)
                   FROM brain.character_personas ORDER BY character_id, version"""
            )
        ).all()
    print()
    for r in rows:
        state = "★ 生效中" if r[2] else "  舊版本"
        print(f"{state}  {r[0]} v{r[1]}  taboos={r[4]}  reviewed_by={r[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
