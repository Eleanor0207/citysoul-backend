"""
集中管理環境設定。
本機開發跟未來接 GCP（Cloud SQL / Memorystore）只需要換 .env，程式碼不用動。
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    database_url: str
    redis_url: str

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
