"""
集中管理環境設定。
本機開發跟未來接 GCP（Cloud SQL / Memorystore）只需要換 .env，程式碼不用動。
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    database_url: str
    redis_url: str

    # 三種 token（SDD第6節）的簽章金鑰刻意分開管理（不同環境變數），
    # 避免共用同一把金鑰、共用同一套驗證邏輯——否則 90 天的 session token
    # 就能拿來冒充 15 分鐘的相遇憑證，在場驗證形同虛設。
    session_token_secret: str
    encounter_token_secret: str
    sense_token_secret: str

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
