import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlayerCreateRequest(BaseModel):
    """
    App 首次啟動時呼叫。device_id 由 App 產生並存在裝置安全儲存區
    （對應 CONTEXT.md「匿名玩家」定義），這裡不接受任何硬體裝置 ID 當替代。
    """

    device_id: str = Field(min_length=1, max_length=128)

    @field_validator("device_id")
    @classmethod
    def device_id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("device_id 不可為空白字元")
        return value


class PlayerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    player_id: uuid.UUID
    device_id: str
    account_id: str | None
    created_at: datetime
    session_token: str


class SummonRequest(BaseModel):
    """
    S2．在場驗證請求（SDD 第8.4節）。

    `gps_accuracy_m` 與 `is_mock_location` 現在只是被接收下來、還沒被使用：
    防作弊觀察期的 log 記錄是 ticket #14 的範圍。先把欄位定義好，App 端才不用
    等 #14 落地才改請求格式。
    """

    spirit_id: str = Field(min_length=1, max_length=64)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    gps_accuracy_m: float | None = None
    is_mock_location: bool = False


class SummonResponse(BaseModel):
    """
    Sprint2 範圍刻意不含 SDD 第8.4節的 `quest` 欄位——quest_progress 表
    要等 S4（ticket #15）才建立。等 #15 落地後再擴充這個 response。
    """

    encounter_token: str
    spirit_id: str


class SpiritResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    place_id: str
    name: str
    latitude: float
    longitude: float
    summon_radius_m: int
    is_active: bool
