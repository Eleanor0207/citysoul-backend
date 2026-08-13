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

    # 開發測試主控台（`/dev/console`）。手動走完「選身分 → 選地標 → 召喚 →
    # 對話」用的頁面，不需要 Unity client。
    #
    # 預設開著，因為本機開發需要它而不該再多一個設定步驟。**正式對外的部署要
    # 明確關掉**：它本身不提供正式 API 以外的權限（切身分一樣走
    # `POST /api/v1/players`），但它把「有哪些玩家、有哪些地標」列出來，那是
    # 內部資訊，也是一個讓人很容易開始拿正式資料庫當測試場的入口。
    dev_console_enabled: bool = True

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

    # 模型輸出上限。放在設定而不是只寫在 prompt 裡——靠 prompt 請模型「請簡短回答」
    # 是沒有保證的，而這個值直接決定成本上限（🔴 高風險「AI 對話成本與延遲」）。
    #
    # 256 → 512（2026-08-14）。人格卡上線、prompt 補上史實與基調之後，模型的回答
    # 從兩三句變成三四段，實測 317 與 331 字都**斷在句子中間**。上限是硬牆，撞到
    # 就是把已經付費生成的內容丟掉，然後給玩家半句話。
    #
    # 真正的修正是在 prompt 裡給長度指示（見 prompt_builder 的 `_length_section`）；
    # 這個值調高是那道指示的安全網，不是替代品。兩者一起做之後，典型輸出反而比
    # 以前短——以前是每次都寫到撞牆為止。
    gemini_max_output_tokens: int = 512
    gemini_timeout_seconds: float = 8.0

    # B2 Prompt 組裝（#12）。SDD §10 標明這兩個數字**待實測調整**，所以它們是
    # 設定而不是常數——組裝邏輯裡不該出現任何字面量，否則調整就要改程式碼。
    #
    # 兩者都直接決定每輪的 prompt 長度，也就是成本與延遲（🔴 高風險項）。
    prompt_memory_top_k: int = 3
    prompt_recent_turns: int = 6

    # B10 TTS（#21）。MVP 語言固定台灣繁體中文。
    #
    # ⚠️ **語言是設定，不是從文字自動偵測。** 自動偵測會讓一句混了英文地名的
    # 台詞被判成英文，然後用英文腔唸出整句中文。玩家聽到的是角色破音，而我們
    # 在 log 上看不到任何錯誤。
    tts_language_code: str = "zh-TW"

    # 留空表示讓 Google 依語言挑預設嗓音。要指定的話用完整名稱
    # （例如 cmn-TW-Wavenet-A）——嗓音是角色的一部分，換嗓音等於換角色，
    # 應該是一次明確的決定而不是預設值漂移的結果。
    tts_voice_name: str | None = None
    tts_timeout_seconds: float = 8.0

    # 音檔存放的 GCS bucket。留空時真實實作會直接降級成「沒有語音」——
    # 本機開發不該為了跑起來而被迫先開一個 bucket。
    tts_audio_bucket: str | None = None

    # 音檔的存活時間。對話語音是一次性的，沒有理由永久保存——那只會累積成本，
    # 而且錄下了玩家聽過什麼。實際的清除靠 bucket 的 lifecycle rule，這個值
    # 是簽章 URL 的有效期。
    tts_audio_url_ttl_seconds: int = 3600

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
