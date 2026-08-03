import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PlayerCreateRequest(BaseModel):
    """
    App 首次啟動時呼叫。device_id 由 App 產生並存在裝置安全儲存區
    （對應 CONTEXT.md「匿名玩家」定義），這裡不接受任何硬體裝置 ID 當替代。
    """

    device_id: str


class PlayerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    player_id: uuid.UUID
    device_id: str
    account_id: str | None
    created_at: datetime


class SpiritResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    place_id: str
    name: str
    latitude: float
    longitude: float
    summon_radius_m: int
    is_active: bool
