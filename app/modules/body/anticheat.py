"""
S3．防作弊觀察期（對應 SDD 第0/7.1節決策3）。

**這個模組只記錄，永遠不阻擋。** 觀察期的目的是先累積數據，之後再決定要不要
真的擋下來。任何一個函式都不該回傳「要不要拒絕這次召喚」，也不該讓例外往外
拋——防作弊壞掉時，正常玩家的召喚必須照常成立（見 `run_observation_checks`
的 try/except）。

## 為什麼速度檢查算的是「地標之間」而不是「GPS 之間」

Ticket #14 的原文寫的是「兩次召喚之間的 GPS 位移／時間差」，但那個做法需要把
上一次召喚的原始座標留到下一次請求，而 CONTEXT.md 對「在場紀錄」的定義是：

    完成在場驗證所需的地標、時間與反作弊結果；
    原始 GPS 座標只在驗證期間使用後即丟棄，不形成移動軌跡

也就是說可以留的是「地標 + 時間」，不能留的正是原始座標。所以這裡存的是
`{spirit_id, 時間}`，下次要算速度時再去 spirits 表查那個地標的座標——地標座標
是公開的靜態資料，不是玩家的位置歷史。

實際效果幾乎一樣：這樣仍然抓得到「三分鐘前在台北召喚、現在人在高雄」這種
瞬移，而那正是速度檢查真正要抓的作弊樣態。抓不到的是「同一個地標附近偽造
小範圍位移」，但那本來就該由 mock location 偵測負責。
"""
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.redis_client import redis_client
from app.modules.body import models
from app.modules.body.geo import haversine_distance_m

ANTICHEAT_LOGGER_NAME = "citysoul.anticheat"
logger = logging.getLogger(ANTICHEAT_LOGGER_NAME)

# 觀察期的兩個可調旋鈕，之後看實際數據再修。
#
# 300 km/h 大致是台灣高鐵的最高營運速度：搭高鐵移動不該被標記，搭飛機或
# 明顯瞬移才會。觀察期只記 log，所以寧可放寬一點保持訊噪比。
MAX_PLAUSIBLE_SPEED_KMH = 300.0
# 上一次召喚的地標記錄保留多久。超過這個時間才再召喚的話，任何速度都算合理，
# 留著也沒意義——順帶讓這筆記錄不會無限期存在。
LAST_SUMMON_TTL_SECONDS = 60 * 60 * 2

EVENT_MOCK_LOCATION = "mock_location"
EVENT_IMPLAUSIBLE_SPEED = "implausible_speed"


def _last_summon_key(player_id: uuid.UUID | str) -> str:
    return f"last_summon:{player_id}"


def _emit(
    event: str,
    *,
    player_id: uuid.UUID | str,
    spirit_id: str,
    detected_at: datetime,
    basis: str,
) -> None:
    """
    統一的 log 格式，四個欄位對應驗收標準：player_id、spirit_id、偵測時間、偵測依據。

    用 `extra` 帶結構化欄位而不是全部塞進訊息字串，之後接 Cloud Logging 時
    這些欄位會直接變成可查詢的 structured log 欄位，不需要回頭改格式或寫 parser。
    """
    logger.warning(
        "anticheat %s: player=%s spirit=%s basis=%s",
        event,
        player_id,
        spirit_id,
        basis,
        extra={
            "anticheat_event": event,
            "player_id": str(player_id),
            "spirit_id": spirit_id,
            "detected_at": detected_at.isoformat(),
            "basis": basis,
        },
    )


def check_mock_location(
    *,
    player_id: uuid.UUID | str,
    spirit_id: str,
    is_mock_location: bool,
    gps_accuracy_m: float | None,
    now: datetime,
) -> None:
    """`is_mock_location=true` 時記一筆 log。false 時什麼都不做。"""
    if not is_mock_location:
        return

    _emit(
        EVENT_MOCK_LOCATION,
        player_id=player_id,
        spirit_id=spirit_id,
        detected_at=now,
        basis=f"client reported is_mock_location=true (gps_accuracy_m={gps_accuracy_m})",
    )


def check_travel_speed(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    spirit: models.Spirit,
    now: datetime,
) -> None:
    """
    跟上一次成功召喚的地標比對移動速度，不合理就記一筆 log。

    比對完之後才把這次的地標記錄覆蓋上去，所以 Redis 裡永遠只有「最近一次」
    一筆，不會累積成軌跡。
    """
    previous = _read_last_summon(player_id)
    if previous is not None:
        _log_if_implausible(db, player_id=player_id, spirit=spirit, previous=previous, now=now)

    _write_last_summon(player_id, spirit_id=spirit.spirit_id, now=now)


def _read_last_summon(player_id: uuid.UUID | str) -> dict | None:
    raw = redis_client.get(_last_summon_key(player_id))
    return json.loads(raw) if raw else None


def _write_last_summon(player_id: uuid.UUID | str, *, spirit_id: str, now: datetime) -> None:
    redis_client.set(
        _last_summon_key(player_id),
        json.dumps({"spirit_id": spirit_id, "at": now.isoformat()}),
        ex=LAST_SUMMON_TTL_SECONDS,
    )


def _log_if_implausible(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    spirit: models.Spirit,
    previous: dict,
    now: datetime,
) -> None:
    if previous.get("spirit_id") == spirit.spirit_id:
        return  # 同一個地標，沒有位移可言

    previous_spirit = (
        db.query(models.Spirit).filter_by(spirit_id=previous.get("spirit_id")).first()
    )
    if previous_spirit is None:
        return  # 上一個地標已被移除，無從比對

    elapsed_seconds = (now - datetime.fromisoformat(previous["at"])).total_seconds()
    if elapsed_seconds <= 0:
        return  # 時鐘異常或同一瞬間，算出來的速度沒有意義

    distance_km = (
        haversine_distance_m(
            previous_spirit.latitude, previous_spirit.longitude, spirit.latitude, spirit.longitude
        )
        / 1000
    )
    speed_kmh = distance_km / (elapsed_seconds / 3600)

    if speed_kmh <= MAX_PLAUSIBLE_SPEED_KMH:
        return

    _emit(
        EVENT_IMPLAUSIBLE_SPEED,
        player_id=player_id,
        spirit_id=spirit.spirit_id,
        detected_at=now,
        basis=(
            f"{speed_kmh:.0f} km/h from {previous_spirit.spirit_id} "
            f"({distance_km:.1f} km in {elapsed_seconds:.0f}s), "
            f"threshold {MAX_PLAUSIBLE_SPEED_KMH:.0f} km/h"
        ),
    )


def run_observation_checks(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    spirit: models.Spirit,
    is_mock_location: bool,
    gps_accuracy_m: float | None,
    now: datetime | None = None,
) -> None:
    """
    `/summon` 的單一進入點。在在場驗證**通過之後**才呼叫。

    整段包在 try/except 裡：觀察期的資料收集再有價值，也不值得讓 Redis 斷線
    或任何一個 bug 把正常玩家的召喚變成 500。抓到例外只記 exception log，
    然後讓召喚照常完成。
    """
    now = now or datetime.now(timezone.utc)
    try:
        check_mock_location(
            player_id=player_id,
            spirit_id=spirit.spirit_id,
            is_mock_location=is_mock_location,
            gps_accuracy_m=gps_accuracy_m,
            now=now,
        )
        check_travel_speed(db, player_id=player_id, spirit=spirit, now=now)
    except Exception:  # noqa: BLE001 — 見 docstring：觀察期不可影響主流程
        logger.exception("anticheat checks failed; summon continues regardless")
