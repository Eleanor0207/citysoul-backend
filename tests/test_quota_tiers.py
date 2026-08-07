"""
配額分級（AC8／#32、migration 0004）。

這裡驗的是 schema 的保證與分級指派，**不含扣配額**——計數器在 Redis，
那是 dialogue 端點成形之後的事。
"""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.body import models
from app.modules.body.quota import default_tier_id, limits_for_tier

CLOSED_BETA = "closed_beta"


def test_closed_beta_tier_exists_after_migration(db_session):
    """
    `closed_beta` 是 migration 的一部分，不是 seed data。

    `players.usage_tier_id` 是 NOT NULL，所以沒有這一列的資料庫連一個匿名玩家
    都建不出來——那是 schema 能不能運作的前提。放進 seed script 的話，忘了跑
    seed 的環境會在「建立玩家」這一步炸掉，而錯誤訊息會指向外鍵而不是指向
    「你少跑了一個步驟」。
    """
    tier = db_session.query(models.UsageTier).filter_by(tier_id=CLOSED_BETA).one()

    assert tier.is_default is True


def test_closed_beta_limits_match_the_agreed_values(db_session):
    """
    封測期初始值。這些數字**只是起點**，改它們應該是改資料庫資料，不是改
    程式碼再發版——這個測試同時也是在說明那件事。
    """
    assert limits_for_tier(db_session, CLOSED_BETA) == {
        "dialogue_calls_daily": 50,
        "daily_tokens": 150_000,
        "prompt_max_chars": 500,
        "api_rate_per_minute": 6,
        "landmark_recognition_daily": 10,
    }


def test_only_one_tier_can_be_default(db_session):
    """
    「只能有一筆 is_default = TRUE」是文件裡寫的一句話，而 `BOOLEAN DEFAULT
    FALSE` 完全擋不住兩筆都是 TRUE。真正在保證那句話的是 partial unique index。
    """
    db_session.add(
        models.UsageTier(tier_id=f"t-{uuid.uuid4().hex[:8]}", display_name="另一個", is_default=True)
    )

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_many_non_default_tiers_are_fine(db_session):
    """
    partial index 只約束 `is_default` 為真的列。非預設分級要能有很多個，
    否則「未來加一個 public_default 分級」這件事會被自己的約束擋住。
    """
    ids = [f"t-{uuid.uuid4().hex[:8]}" for _ in range(3)]
    for tier_id in ids:
        db_session.add(models.UsageTier(tier_id=tier_id, display_name="非預設"))
    db_session.commit()

    try:
        assert db_session.query(models.UsageTier).filter_by(is_default=False).count() >= 3
    finally:
        db_session.query(models.UsageTier).filter(models.UsageTier.tier_id.in_(ids)).delete(
            synchronize_session=False
        )
        db_session.commit()


def test_new_player_gets_the_default_tier(client, db_session, unique_device_id):
    """
    分級的指派來源是 `is_default`，不是程式碼裡的字串常數。這條讓「改預設分級」
    真的只要改資料。
    """
    body = client.post("/api/v1/players", json={"device_id": unique_device_id}).json()

    player = db_session.query(models.Player).filter_by(player_id=body["player_id"]).one()
    assert player.usage_tier_id == default_tier_id(db_session)


def test_adding_a_resource_type_needs_no_schema_change(db_session):
    """
    這個設計的整個賣點：加一種新配額只是 INSERT 一筆。

    `landmark_recognition_daily` 就是真實案例——《地標辨識》文件加這個配額時
    沒有動任何 schema。這裡用一個假的資源類型再示範一次。
    """
    resource = f"made_up_{uuid.uuid4().hex[:8]}"
    db_session.add(
        models.UsageTierLimit(tier_id=CLOSED_BETA, resource_type=resource, limit_value=7)
    )
    db_session.commit()

    try:
        assert limits_for_tier(db_session, CLOSED_BETA)[resource] == 7
    finally:
        db_session.query(models.UsageTierLimit).filter_by(
            tier_id=CLOSED_BETA, resource_type=resource
        ).delete()
        db_session.commit()
