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

    # ⚠️ 不是 asia-east1。實測（2026-08）該區域**一個 Gemini 模型都沒有**，
    # 所有名稱都回 404。SDD 與舊 .env.example 寫的 asia-east1 是錯的。
    gcp_location: str = "global"

    # 實測比較（2026-08，提示詞：龍山寺什麼時候蓋的，要求兩三句）：
    #
    #   模型                     thinking  預算   延遲   計費 token
    #   gemini-3.5-flash         LOW       1024   4.8s   479 thinking ＋ 88 輸出
    #   gemini-3.5-flash         LOW        512   5.2s   截斷（thinking 就吃掉 489）
    #   gemini-2.5-flash-lite    —          256   1.0s   47 輸出，無 thinking
    #
    # 選 lite：兩者都答對了乾隆三年／1738，但玩家站在廟埕前，1 秒與 5 秒的
    # 差別感覺得出來；而每輪多付約 500 個 thinking token 正是 SDD 列為 🔴
    # 高風險的「AI 對話成本與延遲」。ADR-0001 要的也是「單一快速模型」。
    #
    # 要換回 3.5-flash 的話，記得**同時**把 max_output_tokens 調到 1024 以上
    # 並設 thinking_level=LOW，否則會拿到斷在句子中間的回應。
    gemini_model: str = "gemini-2.5-flash-lite"

    # 只有 gemini-3.5 系列接受；2.5 系列傳了會直接回 400 INVALID_ARGUMENT。
    # 預設不傳。
    gemini_thinking_level: str | None = None

    # 模型輸出上限。放在設定而不是 prompt 文字裡——靠 prompt 請模型「請簡短回答」
    # 是沒有保證的，而這個值直接決定成本上限（🔴 高風險「AI 對話成本與延遲」）。
    gemini_max_output_tokens: int = 256
    gemini_timeout_seconds: float = 8.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
