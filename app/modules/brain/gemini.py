"""
B1．Vertex AI Gemini 串接與失敗回退（ADR-0001、ADR-0003）。

## 這個模組最重要的性質

**`generate()` 永遠回傳一個非空字串，永遠不拋例外。**

CONTEXT.md：「無合格輸入或生成失敗時使用人工預寫台詞」。模型暫時不可用不得
中斷召喚流程——玩家已經走到廟埕了，他不該因為我們的雲端服務打嗝而看到錯誤畫面。

所以呼叫端**不需要** try/except：

    reply = client.generate(prompt)   # 這行不會炸

代價是「模型失敗」與「模型回了這句話」在型別上無法區分。這是刻意的取捨；
真的需要區分時（例如觀察成功率）看 log 或 `last_failure_reason`，不要改成拋例外。

## 為什麼用 google-genai 而不是 vertexai.generative_models

實測（2026-08，專案 citysoul）：舊的 `vertexai.generative_models` **看不到
`gemini-3.5-flash`**，所有呼叫回 404 model not found；換成 `google-genai`
同一個專案、同一個區域就拿得到。兩套 SDK 的模型可見度不一樣，不要用 404
來判斷「這個模型不存在」。

## 憑證

沒有任何憑證參數，見 ADR-0003。走 Application Default Credentials：
本機 `gcloud auth application-default login`，正式環境是 Cloud Run 綁定的
service account。程式碼在兩邊完全相同。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from typing import Any, Callable

import re

from app.core.config import settings
from app.core.text_normalize import strip_fold_spaces

logger = logging.getLogger(__name__)

# 逾時用的執行緒池。
#
# ⚠️ SDK 的 `generate_content()` 沒有 per-call timeout，所以上限只能加在外面。
# 代價很明確：逾時之後那個執行緒仍在等 HTTP 回應，我們只是不再理會它——
# 真正的取消要等 SDK 支援。
#
# 這樣做仍然值得：玩家站在廟埕前，等 30 秒跟沒有回應是一樣的。
_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="gemini")

# 人工預寫的回退台詞。
#
# 刻意寫得像角色會說的話，而不是「系統忙碌中，請稍後再試」——玩家不該被拉出
# 情境。它同時是「我沒有答案」的合理表達，所以就算模型其實是好的、只是這次
# 逾時，這句話出現也不突兀。
#
# 這句話跟 router.py 的 FALLBACK_REPLY 是同一句，但**刻意各自持有**：
# 那邊是「沒命中預寫招呼」的回答，這邊是「模型失敗」的回答。兩者現在恰好
# 相同，但它們會因為不同的理由被改寫。
FALLBACK_REPLY = "這件事我還沒想清楚。要不要先跟我說說你眼前看到的？"


# 「這個參數沒有傳」與「明確傳 None」是兩件事。
#
# `thinking_level=None` 的意思是**不要送 thinking_config**——2.5 系列收到會回
# 400。用 None 當預設值的話這個意圖表達不出來（會被當成「沒傳，去讀設定」），
# 而設定現在的值是 LOW。2026-08-22 換模型時踩到的正是這個。
_UNSET = object()


class GeminiClient(ABC):
    """
    對話生成的抽象介面。

    呼叫端（B2 對話組裝、B9 當日情境）只依賴這個介面，因此測試可以注入
    `FakeGeminiClient` 而**完全不需要 GCP 憑證**。
    """

    @abstractmethod
    def generate(self, prompt: str, *, expect_chinese: bool = False) -> str:
        """產生回應。失敗時回傳 `FALLBACK_REPLY`，不拋例外。

        `expect_chinese=True` 用在**玩家會直接讀到的敘事**：混進英文時重生一次，
        第二次仍然混進去就回退。分類器與 JSON 那類呼叫不要開（見
        `VertexAIGeminiClient._latin_leak`）。
        """

    def for_classification(self) -> "GeminiClient":
        """
        同一個模型、給**只回一個標籤**的呼叫用的變體。

        預設回自己：fake 與測試不需要知道有這回事。真實 client 覆寫它，
        關掉 thinking 並把輸出上限壓到一個標籤的長度——B4 的分類串在生成
        之前，它省下來的每一毫秒都是玩家在等的時間。
        """
        return self


class VertexAIGeminiClient(GeminiClient):
    """
    真實實作。ADR-0001：MVP 只用單一快速模型，不做 Flash/Pro 分流。

    SDK 在需要時才 import，不在模組頂端——這樣沒裝 `google-genai` 的環境
    仍然可以 import 這個模組並使用 fake。
    """

    def __init__(
        self,
        *,
        model_name: str | None = None,
        max_output_tokens: int | None = None,
        timeout_seconds: float | None = None,
        thinking_level: str | None | Any = _UNSET,
        client_factory: Callable[[], Any] | None = None,
    ):
        """
        `client_factory` 讓測試注入一個會拋例外的假 client，藉此驗證**這個類別的**
        回退邏輯。用一個自己就回傳 fallback 的假 GeminiClient 是測不到這條路徑的
        ——那只會驗證假物件本身。
        """
        self._model_name = model_name or settings.gemini_model
        self._max_output_tokens = max_output_tokens or settings.gemini_max_output_tokens
        self._timeout_seconds = timeout_seconds or settings.gemini_timeout_seconds
        self._thinking_level = (
            settings.gemini_thinking_level if thinking_level is _UNSET else thinking_level
        )
        self._client_factory = client_factory or self._create_client
        self._client = None
        self._classification_client: "VertexAIGeminiClient | None" = None

        self.last_failure_reason: str | None = None
        self.last_truncated: bool = False
        self.last_latin_leak: list[str] = []
        self.last_simplified_leak: list[str] = []

    # 分類呼叫的輸出上限。標籤最長是 `religious_doctrine`，遠在這個數字之內；
    # 留餘裕是因為撞到上限會被判成截斷，而截斷在 B4 是 fail-closed（誤擋玩家）。
    _CLASSIFICATION_MAX_TOKENS = 32

    def for_classification(self) -> "GeminiClient":
        """
        關掉 thinking、壓低輸出上限的同一個模型。

        對話用的設定（`thinking_level=LOW`、`max_output_tokens=1536`）是為了
        寫出三四段敘事而調的。B4 要的是一個單字標籤，那些預算換不到任何準確度，
        只換到玩家在生成之前多等的一段時間。

        每次呼叫都重建一個 client 太浪費，所以快取在實例上。
        """
        if self._classification_client is None:
            self._classification_client = VertexAIGeminiClient(
                model_name=self._model_name,
                max_output_tokens=self._CLASSIFICATION_MAX_TOKENS,
                timeout_seconds=self._timeout_seconds,
                thinking_level=None,
                client_factory=self._client_factory,
            )
        return self._classification_client

    def _create_client(self):
        import google.auth
        from google import genai

        # 沒有金鑰檔：ADC 自己找（ADR-0003）。
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )

        return genai.Client(
            enterprise=True,
            project=settings.gcp_project_id,
            location=settings.gcp_location,
            credentials=credentials,
        )

    def _ensure_client(self):
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def _build_config(self):
        from google import genai

        kwargs: dict[str, Any] = {
            # 長度上限在**呼叫參數**層級，不是靠 prompt 請模型自律。
            # 這個值同時是單次呼叫的成本上限。
            "max_output_tokens": self._max_output_tokens,
        }

        # thinking_level 只有 gemini-3.5 系列接受；2.5 系列傳了會直接回 400。
        # 所以預設是 None（不傳），要用哪個模型就配哪個設定。
        if self._thinking_level:
            kwargs["thinking_config"] = genai.types.ThinkingConfig(
                thinking_level=self._thinking_level
            )

        return genai.types.GenerateContentConfig(**kwargs)

    def generate(self, prompt: str, *, expect_chinese: bool = False) -> str:
        """
        產生一段回應。

        ## 為什麼混進英文要重生，而不是在輸出端砍掉

        prompt 已經明說「整段回話裡不出現任何英文字母」，但那是請求不是保證。
        實測漏出有兩種：夾一個詞（「stories 說也說不完」），以及**整段翻成英文**
        （2026-08-22 龍山寺回了一整段 "those pillars… still smelling of wood"）。
        後者用正規表示式砍不掉——砍完就沒有回應了。

        所以是重生一次，不是修剪。重生保留「模型輸出不被我們改寫」這條線，而
        修剪會誤傷 MOCA、IMAX 這種地標本身就有的名字。

        ## 簡體字用同一條路處理

        2026-08-23 實測漏出「還→还」。簡體比英文嚴重：客戶端字型
        （`NotoSansTC-VF.ttf`）只收繁中，簡體字**一個都畫不出來**，玩家看到的是
        方框而不是看得懂的錯字。偵測清單正是從那份字型的 cmap 推出來的。

        ⚠️ 只重生**一次**。第二次仍然漏就回退到人工預寫台詞：再試下去是拿玩家
        的等待時間與我們的帳單去賭一個機率性的結果。
        """

        first = self._generate_once(prompt)
        if not expect_chinese or first == FALLBACK_REPLY:
            return first

        leaked = self._latin_leak(first)
        simplified = self._simplified_leak(first)
        if not leaked and not simplified:
            return first

        logger.warning(
            "回應混進不該有的字（英文 %s／簡體 %s，模型 %s），重生一次。",
            leaked,
            simplified,
            self._model_name,
        )
        second = self._generate_once(prompt)
        if second == FALLBACK_REPLY:
            return second

        still_leaked = self._latin_leak(second)
        still_simplified = self._simplified_leak(second)
        if not still_leaked and not still_simplified:
            return second

        self.last_latin_leak = still_leaked
        self.last_simplified_leak = still_simplified
        return self._fall_back(
            f"連續兩次混進英文 {still_leaked} 或簡體 {still_simplified}"
            f"（模型 {self._model_name}）"
        )

    def _generate_once(self, prompt: str) -> str:
        self.last_failure_reason = None
        self.last_truncated = False
        self.last_latin_leak = []
        self.last_simplified_leak = []

        try:
            client = self._ensure_client()

            future = _EXECUTOR.submit(
                client.models.generate_content,
                model=self._model_name,
                contents=prompt,
                config=self._build_config(),
            )

            # 刻意不用 `future.result(timeout=...)`：Python 3.11 起
            # `concurrent.futures.TimeoutError` **就是**內建的 `TimeoutError`，
            # 所以模型自己拋 TimeoutError 時會跟「我們主動放棄」混在一起，
            # last_failure_reason 會記錯。回退行為相同，但觀測數據會說謊。
            done, _ = futures_wait([future], timeout=self._timeout_seconds)

            if not done:
                return self._fall_back(f"超過 {self._timeout_seconds} 秒未回應")

            # 模型自己拋的例外在這裡重新浮現，交給外層的 except 處理。
            response = future.result()

            text = (getattr(response, "text", None) or "").strip()

            # 截斷偵測。實測 gemini-3.5-flash 在 512 tokens 下會回
            # 「那是在清乾隆三年（西元1738年），先民們懷抱」——非空、看起來成功，
            # 但斷在句子中間。原因是 thinking 先吃掉了約 500 個 token。
            #
            # 仍然回傳這段文字：半句話通常還是比通用的回退台詞有用。但要留下
            # 紀錄，因為這個訊號的正確處置是**調高預算或換模型**，不是在這裡
            # 加一層「切到最後一個句號」的修剪 heuristic——那只會把問題藏起來。
            if self._was_truncated(response):
                self.last_truncated = True
                logger.warning(
                    "Gemini 回應被 max_output_tokens=%s 截斷（模型 %s）。"
                    "考慮調高預算或改用不需要 thinking 的模型。",
                    self._max_output_tokens,
                    self._model_name,
                )

            if not text:
                # 空回應在型別上是「成功」，但對玩家而言跟失敗沒有差別。
                # 常見原因是被安全過濾器擋掉，或 thinking 吃光了整個預算。
                return self._fall_back("模型回傳空字串（安全過濾器或 token 預算耗盡）")

            # 中文標點後面的空格。模型會寫出「什麼沒見過。 1815 年」這種
            # 排版——句號後的空格在中文裡沒有意義，但它會一路帶到玩家眼前，
            # 也會被 TTS 讀成一個停頓。
            #
            # 用的是匯入器那支正規化器，規則一模一樣（只動中日韓字元之間與
            # 中文標點之後的空白，「1945 年」的空格保留）。**不做其他修剪**——
            # 這裡是模型輸出，動得愈少愈好，切句號、補標點那類 heuristic 只會
            # 把生成品質的問題藏起來。
            text = strip_fold_spaces(text)
            self.last_latin_leak = self._latin_leak(text)
            return text

        except Exception as exc:  # noqa: BLE001
            # 刻意攔截所有例外。這裡不該有「哪些例外算預期」的清單——
            # 任何未預期的例外都不是讓召喚流程中斷的理由，而清單一定會漏。
            return self._fall_back(f"{type(exc).__name__}: {exc}")

    # 專有名詞白名單。這些出現在回應裡是正常的——地標本身就叫這個名字。
    _ALLOWED_LATIN = {"moca", "imax", "ar", "vr", "bot", "led"}

    # 簡體字。**這份清單是從客戶端字型的 cmap 推出來的**，不是憑印象列的：
    # `NotoSansTC-VF.ttf` 只收 20,745 個繁中字元，下面每一個字它都畫不出來。
    #
    # 所以漏出簡體不只是「用字不對」，是玩家會看到**一整排方框**——比混進英文
    # 嚴重，英文至少讀得出來。
    #
    # ⚠️ 不完備，也不打算完備：Unicode 沒有「簡體」這個屬性，判斷簡繁需要一份
    # 對照表（opencc 那類）。這裡收的是模型真的會漏的高頻字（2026-08-23 實測
    # 漏出「还」），成本是一個 frozenset 的查表。要更準就得加一個相依，
    # 而那要先量得出漏網率有多高——目前量不出來。
    # 第二類：**字型畫得出來、但仍然是簡體**。上面那份清單漏掉它們，因為它是從
    # 字型 cmap 推的——這幾個字剛好在 20,745 個字元裡。
    #
    # ⚠️ 只收在繁中文本裡**幾乎不可能正確**的。刻意排除掉這些看起來很像簡體、
    # 其實在台灣是正字的：
    #   台（台北）、范（范姓）、松（松樹）、沈（沈姓）、制（制度）、
    #   向、致、板、号
    # 擋了它們等於每次講到台北都要重生一次。
    _SIMPLIFIED_ALSO_IN_FONT = frozenset("个从么无与发样叶复")

    _SIMPLIFIED_HAN = frozenset(
        "专丛丝严丧临为举乐习乡书买乱亏产亩亲仓们伞伟传伪佥侦偿兰关写军击凿刘则刚创别剧劝"
        "办励劳势勋区单卖卢卫厅历厉压厌县叹员哑喷嘱围图圆场垦壮壶夺奖娄婶孙审宾寝对导将尝"
        "层屿岂岛巩帅师帐帧应开弃张弯归当录忧态恳恼悬悯慑戏战执扩扫报拦挚挠挣掷摄摊攒敌斩"
        "断时昼暂术杀杂权来杨枣栏树桨桩检椭欢欧歼殴毕汇汤泽浆涛涨涩渍渔渗湾湿满滤灭烂烛烫"
        "热爱爷牵牺状犹狮狰猎环现疗疟疡瘫皱盏盐盘睁瞩矿砖砚础硕积称稳穷窍签简粤紧约纫纳纵"
        "纸纹线练组绅细终绍结绝继绪续绳绽缘罗罢聂职肾肿胀脏脑脓腾艰艳节获营虏虑虽蚀蚁蝇衬"
        "观觉誉誊认讨让议讲许证诅识诊诌译试诗详语诱说诺课谁谈谓谢谣贞责质贪贫购费贼赂赃资"
        "赔赚赠赡赵趋躯转软轰轻载辖辗辽过还这进远违迟选邓邹郑郧酝酱酿释针钥钻铀铡铣锁锌锥"
        "闪问闷闸闹闻阅阎队阴阶陆陈陕隐难雾韧项顺须顾预颐颖题颜额饮饰饲饶驱驶驻骂骆验鲁鲜"
        "鸡鸣鸦鹅"
    )

    # 檢查用的聯集。兩份清單分開留著是因為出處不同（字型畫不出來／字型畫得出來
    # 但仍是簡體），檢查時沒有差別。
    _SIMPLIFIED = _SIMPLIFIED_HAN | _SIMPLIFIED_ALSO_IN_FONT

    def _latin_leak(self, text: str) -> list[str]:
        """
        這段文字裡不該出現的英文單字。純判斷，不改文字也不記 log。

        ⚠️ **只對玩家會讀到的敘事有意義**，所以由呼叫端用 `expect_chinese` 開啟。
        2026-08-22 之前這個檢查對每一次 `generate()` 都跑，於是 B4 分類器回的
        `safe`、導引提問回的 JSON 鍵名全部被記成「漏出」——那些是正確輸出。
        後果不只是 log 吵：真正的玩家可見漏出被埋在裡面，漏出率完全量不出來。
        """
        return [
            w for w in re.findall(r"[A-Za-z]{2,}", text)
            if w.lower() not in self._ALLOWED_LATIN
        ]

    def _simplified_leak(self, text: str) -> list[str]:
        """
        這段文字裡的簡體字。純判斷，不改文字。

        跟 `_latin_leak` 一樣由 `expect_chinese` 開啟——分類器與 JSON 那類呼叫
        不需要，而且它們的輸出本來就不是給玩家讀的。

        去重並保持出現順序：log 要能一眼看出「漏了哪幾個字」，重複列出同一個字
        只是噪音。
        """
        return list(dict.fromkeys(ch for ch in text if ch in self._SIMPLIFIED))

    @staticmethod
    def _was_truncated(response) -> bool:
        """
        回應是否因為 token 上限被切斷。

        對 finish_reason 的形狀刻意寬鬆：不同 SDK 版本可能是 enum、int 或字串，
        而這個判斷只影響一行 log 與一個觀測旗標，不值得為它綁死內部型別。
        判斷不出來就當作沒截斷。
        """
        try:
            candidates = getattr(response, "candidates", None) or []
            if not candidates:
                return False
            return "MAX_TOKENS" in str(getattr(candidates[0], "finish_reason", ""))
        except Exception:  # noqa: BLE001
            return False

    def _fall_back(self, reason: str) -> str:
        self.last_failure_reason = reason
        logger.warning("Gemini 呼叫失敗，回退人工預寫台詞：%s", reason)
        return FALLBACK_REPLY


class FakeGeminiClient(GeminiClient):
    """
    測試用。放在正式程式碼而不是 tests/ 底下，是因為 B2、B9、對話端點的測試
    都會用到它——放在 tests/ 會變成跨測試檔案 import，那種相依很快就會亂掉。
    """

    def __init__(self, response: str = "（測試用回應）", responses: list[str] | None = None):
        """`responses` 依序回傳，用完之後固定回最後一句——測「重生一次」那條路徑。"""
        self.response = response
        self.responses = list(responses) if responses else None
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, expect_chinese: bool = False) -> str:
        self.prompts.append(prompt)
        if self.responses:
            index = min(len(self.prompts) - 1, len(self.responses) - 1)
            return self.responses[index]
        return self.response

    @property
    def call_count(self) -> int:
        return len(self.prompts)

def split_into_segments(text: str) -> list[str]:
    """
    把回應依空行切成段落，給客戶端逐段推播。

    ⚠️ **分段不會修好被截斷的回應。** 半句話切成兩段還是半句話——那是
    `max_output_tokens` 與 prompt 長度指示要解決的問題（見 `_length_section`）。
    這裡只負責呈現節奏：一次跳出三段文字很像在讀說明書，逐段出現才像有人在講話。

    切空行而不是切句號：段落是模型自己分的意群，句號會把一段話剁成零碎的短句，
    反而讓節奏變得急促。

    永遠回傳至少一個元素——空字串進來就回傳空清單，讓呼叫端不必分辨
    「沒有段落」與「一個空段落」。
    """
    if not text or not text.strip():
        return []
    parts = [p.strip() for p in re.split(r"\n[ \t]*\n", text.strip())]
    return [p for p in parts if p]
