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

    # GCP（ADR-0003）。這裡**沒有**任何憑證欄位，是刻意的——存取 Vertex AI 走
    # Application Default Credentials：正式環境用 Cloud Run 綁定的 service
    # account，本機用 `gcloud auth application-default login`。兩者在程式裡是
    # 同一條路徑，不需要分支，也沒有金鑰檔可以外洩。
    #
    # 底下三個都只是「呼叫哪個模型」，不是秘密，進 git 沒有問題。
    gcp_project_id: str = "citysoul"
    gcp_location: str = "asia-east1"
    # ⚠️ 這個字串沒有對照過 Vertex AI 上真正可用的模型清單。第一次真實呼叫
    # 若回 404 / model not found，先用 `gcloud ai models list --region=...`
    # 確認名稱，不要往程式邏輯裡找原因。
    gemini_model: str = "gemini-3.5-flash"

    # 模型輸出上限。放在設定而不是 prompt 文字裡——靠 prompt 請模型「請簡短回答」
    # 是沒有保證的，而這個值直接決定成本上限（🔴 高風險「AI 對話成本與延遲」）。
    gemini_max_output_tokens: int = 256
    gemini_timeout_seconds: float = 8.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
