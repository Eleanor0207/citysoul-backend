"""把 `content/personas/*.yaml` 的人格草稿匯入 `brain.character_personas`。

用法：
    python -m scripts.import_personas --dry-run
    python -m scripts.import_personas

## 🔒 這支腳本永遠不會把 active 設成 true

CONTEXT.md 對「人格卡」的定義是「經人工審核的角色定義」。**這裡沒有 `active`
這個參數，也沒有任何寫入它的 SQL**——不是忘了做，是刻意讓「自動上線一份沒人看過
的人格」在這條路徑上不可能發生。

YAML 裡若出現 `active` 欄位，匯入器會直接拒絕整批，而不是忽略它。忽略等於接受
一份宣稱自己已審核的檔案，只是靜靜地不照做。

## append 新版本，永不就地覆寫

人格是有版本的（主鍵 `(character_id, version)`）。改人格＝寫一筆新 version，
舊版留著。沒有歷史與審核人記錄的話，出事時無法回溯是誰在什麼時候改的——那不是
稽核的方便，是人格內容能不能上線的前提。

所以同一個 `(character_id, version)` 已存在時，這支腳本**跳過**它，不更新也不
報錯。要改內容就換一個 version 號碼。

跳過而不是報錯，是因為十份人格是一批：其中一份已經匯入過就讓整批失敗的話，
每加一個新地標都要先手動挑掉已完成的檔案。「不覆寫」的保證仍然成立——被跳過的
那一份在資料庫裡一個字都不會變。

## taboos 只能往上加

宗教場域的禁忌是安全下限，敘事審查只能疊加。新版本若少了前一版的任何一條，
匯入器直接拒絕——這條規則寫在程式裡而不是只寫在文件裡，因為它是最容易在
「順手潤稿」時被弄掉的東西，而且弄掉之後看起來完全正常。

## canned_greetings 跟著版本走

外鍵指向 `(character_id, version)`。預寫台詞是**唯一不經過 LLM、原樣送到玩家
眼前的內容**，改台詞就是改人格內容，要走同一條「新版本 → 重新審核」的路。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import yaml
from sqlalchemy import create_engine, text

from app.core.config import settings
from scripts.text_normalize import strip_fold_spaces

PERSONA_DIR = pathlib.Path(__file__).resolve().parent.parent / "content" / "personas"

REQUIRED = ("character_id", "version", "archetype", "speech_style", "reviewed_by")


def validate(path: pathlib.Path, data: dict, conn) -> list[str]:
    errors: list[str] = []
    where = path.name

    for key in REQUIRED:
        if not data.get(key):
            errors.append(f"{where}：缺少必要欄位 `{key}`")

    # 🔒 見 docstring：出現 active 就拒絕，不是忽略。
    if "active" in data:
        errors.append(
            f"{where}：YAML 不得包含 `active`。人格是否生效只能由人工審核流程"
            " 在資料庫裡 flip，不能由檔案宣告"
        )

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

        # taboos 必須是前一版的超集
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


INSERT_PERSONA = text(
    """
    INSERT INTO brain.character_personas
        (character_id, version, archetype, speech_style, personality_traits, values,
         taboos, not_this_character, imagination_license, quest_themes, tone_override,
         reviewed_by, reviewed_at, active)
    VALUES
        (:character_id, :version, :archetype, :speech_style, :personality_traits, :values,
         :taboos, :not_this_character, :imagination_license, :quest_themes, :tone_override,
         :reviewed_by, now(), false)
    """
)
# ⚠️ 上面那個 `false` 是寫死的，不是參數。見 docstring。

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
    errors: list[str] = []
    with engine.connect() as conn:
        skipped: list[str] = []
        for path in files:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            # YAML 折行在中文之間留下的空格，見 scripts/text_normalize.py
            data = strip_fold_spaces(data)
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
                    "tone_override": d.get("tone_override"),
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

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """SELECT character_id, version, active, reviewed_by,
                          cardinality(taboos)
                   FROM brain.character_personas ORDER BY character_id, version"""
            )
        ).all()
    print("\nbrain.character_personas：")
    for r in rows:
        state = "生效中" if r[2] else "草稿"
        print(f"  {r[0]} v{r[1]}  {state}  taboos={r[4]}  reviewed_by={r[3]}")
    print("\n🔒 新匯入的版本一律 active=false。要上線需人工審核後手動 flip。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
