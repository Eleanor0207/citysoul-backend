"""
封閉測試垂直切片的 seed data。

CONTEXT.md：「以單一龍山寺靈魂驗證核心迴圈...通過驗證後才擴展到首發靈魂集合」。
所以這裡刻意只 seed 一座城市、一個地標、一個角色、一版**未審核**的人格草稿，
不要因為方便就把十個首發靈魂都塞進來——那是垂直切片驗證通過之後才做的事。

`closed_beta` 配額分級**不在這裡**，它在 migration 0004 裡。`players.usage_tier_id`
是 NOT NULL，沒有那筆資料連匿名玩家都建不出來，那是 schema 的前提而不是範例資料。
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.modules.body.models import Spirit
from app.modules.brain.models import Character, CharacterPersona, CitySoul, LandmarkSoul

# 沿用 slug 慣例，而不是真正的 Google Place ID（那是 `ChIJ` 開頭的一長串）。
#
# ⚠️ 這跟 SDD §7.3.1 的 Addressables key `spirit_longshan` 是**兩個不同的識別碼**：
# 前者是後端的靈魂主鍵，後者是客戶端載入 3D 模型用的資源鍵。
LONGSHAN_SPIRIT_ID = "longshan_temple"

TAIPEI_CITY_ID = "taipei"
LONGSHAN_LANDMARK_ID = "longshan_temple"
LONGSHAN_CHARACTER_ID = "longshan_watcher"

# ⚠️ 龍山寺是**活的宗教場所**，不是天文館換個名字而已。CONTEXT.md「史實邊界」
# 要求不對敏感議題作武斷定論；在宗教場域，那具體意味著不能代神明發言、不能
# 給命運指示。這幾條是**安全下限**，敘事負責人只能往上加，不能拿掉——新版本
# 人格若少了其中任何一條，審核流程應視為不通過。
LONGSHAN_TABOOS = [
    "代替神明給予指示或應許",
    "個人吉凶、姻緣、財運的預測",
    "宗教或信仰之間的優劣比較",
    "具體的醫療、法律、投資建議",
]

_PENDING = "PENDING_NARRATIVE_REVIEW"


def _seed_city(db: Session) -> None:
    if db.query(CitySoul).filter_by(city_id=TAIPEI_CITY_ID).first():
        return
    db.add(
        CitySoul(
            city_id=TAIPEI_CITY_ID,
            name="臺北",
            macro_history_summary=_PENDING,
            core_tone_descriptors=[_PENDING],
            shared_values=[_PENDING],
        )
    )


def _seed_landmark(db: Session) -> None:
    if db.query(LandmarkSoul).filter_by(landmark_id=LONGSHAN_LANDMARK_ID).first():
        return
    db.add(
        LandmarkSoul(
            landmark_id=LONGSHAN_LANDMARK_ID,
            city_id=TAIPEI_CITY_ID,
            name="艋舺龍山寺",
            # 史實層決定角色「知道什麼」。內容待敘事負責人撰寫；佔位字串刻意
            # 留成 PENDING 而不是隨手填一段，免得看起來已經完成。
            founding_facts=[{"year": _PENDING, "event": _PENDING, "detail": _PENDING}],
            key_events=None,
            cultural_significance=_PENDING,
        )
    )


def _seed_character(db: Session) -> None:
    if not db.query(Character).filter_by(character_id=LONGSHAN_CHARACTER_ID).first():
        db.add(
            Character(character_id=LONGSHAN_CHARACTER_ID, landmark_id=LONGSHAN_LANDMARK_ID)
        )

    existing = (
        db.query(CharacterPersona)
        .filter_by(character_id=LONGSHAN_CHARACTER_ID, version=1)
        .first()
    )
    if existing:
        return

    db.add(
        CharacterPersona(
            character_id=LONGSHAN_CHARACTER_ID,
            version=1,
            archetype="沉靜、耐心，對往來人群的祈願有長久記憶的守望者",
            speech_style="溫和、不疾不徐，帶市井氣但不輕浮",
            personality_traits=["沉靜", "耐心", "不評斷"],
            values=["艋舺的市井生活", "世代更迭", "人們帶來的心事"],
            taboos=list(LONGSHAN_TABOOS),
            # 負面人格聲明。跟 taboos 是兩件事：taboos 說「不談什麼」，
            # 這裡說「不是誰」。在宗教場域這條界線特別要講清楚。
            not_this_character="不是廟方人員，不是神明本身，也不是解籤者",
            imagination_license="神祕感來自時間累積的記憶本身；不宣稱靈驗、不預言吉凶",
            quest_themes=[],
            # 草稿。**沒有任何程式碼路徑會把 active 設成 True**，
            # 只有人工審核流程能 flip。
            active=False,
            reviewed_by="PENDING_HUMAN_REVIEW",
            reviewed_at=datetime.now(timezone.utc),
        )
    )


def _seed_spirit(db: Session) -> None:
    if db.query(Spirit).filter_by(spirit_id=LONGSHAN_SPIRIT_ID).first():
        return
    db.add(
        Spirit(
            spirit_id=LONGSHAN_SPIRIT_ID,
            display_name="艋舺龍山寺",
            character_id=LONGSHAN_CHARACTER_ID,
            landmark_id=LONGSHAN_LANDMARK_ID,
            # 廟埕前廣場。CONTEXT.md「召喚點」要求安全、公開、不要求進入
            # 受管制場館——所以定在廣場而不是殿內。
            latitude=25.0373983,
            longitude=121.4997318,
            summon_radius_meters=50,
            sense_radius_meters=150,
            is_active=True,
        )
    )


def seed_vertical_slice(db: Session) -> None:
    # 順序有相依：city → landmark → character → spirit（spirit 引用前兩者的 id）。
    _seed_city(db)
    _seed_landmark(db)
    db.flush()
    _seed_character(db)
    _seed_spirit(db)
    db.commit()
