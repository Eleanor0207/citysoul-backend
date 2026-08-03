"""
集中管理環境設定。
本機開發跟未來接 GCP（Cloud SQL / Memorystore）只需要換 .env，程式碼不用動。
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    database_url: str
    redis_url: str

    # Session Token（S6／SDD第6節）簽章金鑰，刻意跟未來的 Encounter/Sense Token
    # 分開管理（不同環境變數），避免共用同一把金鑰、共用同一套驗證邏輯。
    session_token_secret: str

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
