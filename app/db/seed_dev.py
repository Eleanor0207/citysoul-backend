"""
開發測試用的第二個靈魂：剝皮寮。

## 為什麼不寫進 `seed.py`

`seed_vertical_slice` 是垂直切片的資料，它的 docstring 明寫「刻意只 seed 一座
城市、一個地標、一個角色」，理由是 CONTEXT.md 要求先用單一龍山寺靈魂驗證核心
迴圈、通過之後才擴展到首發靈魂集合。把第二個靈魂加進那支函式，等於在沒有人做
那個決定的情況下把它推翻。

這支的用途窄得多：**客戶端要驗「地圖上不同召喚點各自進到不同角色」這條路**，
而那需要至少兩個靈魂存在。它不是首發靈魂集合的一部分，也不代表剝皮寮的內容
已經開始製作。

## 人格是空的，而且是刻意的

`active=False`、`reviewed_by=PENDING_HUMAN_REVIEW`，敘述欄位全是 PENDING——
跟龍山寺草稿同一套處理。沒有任何程式路徑會把 `active` 翻成 True，只有人工審核
流程能。所以拿這個靈魂對話會走預寫招呼/fallback，不會生出沒人審過的角色台詞。

跑法（不會動到既有資料，重複跑是安全的）：

    uv run python -m scripts.seed_dev
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.seed import TAIPEI_CITY_ID, _PENDING
from app.modules.body.models import Spirit
from app.modules.brain.models import Character, CharacterPersona, LandmarkSoul

# 識別碼是 citysoul-doc 的 `landmark/README.md` §2 在 2026-08-13 定案的那一組。
# 這支原本用的是較短的 `bopiliao`，跟定案不一致——而 `content/spirits.yaml`
# 走的是定案值，兩邊並存會讓匯入器把 `bopiliao_keeper` 改指到另一個 landmark，
# 憑空多出一隻重複的靈魂。改齊的是這一邊，因為偏離的是這一邊。
#
# ⚠️ 召喚點的 key 仍然是 `bopiliao`（近景索引裡的那個），跟 spirit_id 不同名。
# 客戶端的 `SpiritCatalog` 是對照表，就是為了容納這種不一致。
BOPILIAO_SPIRIT_ID = "bopiliao_historic_block"
BOPILIAO_LANDMARK_ID = "bopiliao_historic_block"
BOPILIAO_CHARACTER_ID = "bopiliao_keeper"

# 召喚點座標取自客戶端的 `tiles/near/index.json`——兩邊必須是同一個點，
# 否則玩家站在地圖 pin 上時後端會判定不在半徑內，而那個矛盾很難從任一邊看出來。
BOPILIAO_LATITUDE = 25.0368459
BOPILIAO_LONGITUDE = 121.5022679


def _seed_landmark(db: Session) -> None:
    if db.query(LandmarkSoul).filter_by(landmark_id=BOPILIAO_LANDMARK_ID).first():
        return
    db.add(
        LandmarkSoul(
            landmark_id=BOPILIAO_LANDMARK_ID,
            city_id=TAIPEI_CITY_ID,
            name="剝皮寮歷史街區",
            founding_facts=[{"year": _PENDING, "event": _PENDING, "detail": _PENDING}],
            key_events=None,
            cultural_significance=_PENDING,
        )
    )


def _seed_character(db: Session) -> None:
    if not db.query(Character).filter_by(character_id=BOPILIAO_CHARACTER_ID).first():
        db.add(
            Character(
                character_id=BOPILIAO_CHARACTER_ID, landmark_id=BOPILIAO_LANDMARK_ID
            )
        )

    existing = (
        db.query(CharacterPersona)
        .filter_by(character_id=BOPILIAO_CHARACTER_ID, version=1)
        .first()
    )
    if existing:
        return

    db.add(
        CharacterPersona(
            character_id=BOPILIAO_CHARACTER_ID,
            version=1,
            # 全部 PENDING，不先寫一版「暫時的」人格：暫時的東西一旦看起來能用，
            # 就不會有人去寫真的那一版。
            archetype=_PENDING,
            speech_style=_PENDING,
            personality_traits=[_PENDING],
            values=[_PENDING],
            taboos=[_PENDING],
            not_this_character=_PENDING,
            imagination_license=_PENDING,
            quest_themes=[],
            active=False,
            reviewed_by="PENDING_HUMAN_REVIEW",
            reviewed_at=datetime.now(timezone.utc),
        )
    )


def _seed_spirit(db: Session) -> None:
    if db.query(Spirit).filter_by(spirit_id=BOPILIAO_SPIRIT_ID).first():
        return
    db.add(
        Spirit(
            spirit_id=BOPILIAO_SPIRIT_ID,
            display_name="剝皮寮歷史街區",
            character_id=BOPILIAO_CHARACTER_ID,
            landmark_id=BOPILIAO_LANDMARK_ID,
            latitude=BOPILIAO_LATITUDE,
            longitude=BOPILIAO_LONGITUDE,
            # 跟龍山寺同一組半徑。客戶端的三段式標記門檻（50／150）是照這兩個
            # 數字寫的，各地標用不同半徑之前先讓它們一致。
            summon_radius_meters=50,
            sense_radius_meters=150,
            is_active=True,
        )
    )


def seed_dev_extras(db: Session) -> None:
    # 順序有相依：landmark → character → spirit。city 由垂直切片那支建立，
    # 這裡假設它已經在——沒有的話代表 DB 根本沒初始化過，該先跑 init_db。
    _seed_landmark(db)
    db.flush()
    _seed_character(db)
    _seed_spirit(db)
    db.commit()
