"""
B5．史實邊界規則注入（issue #19）。

產出一段「不對敏感歷史武斷定論」的規則文字，供 B2 Prompt 組裝引擎（#12）注入
System Instruction 的**第 2 順位**（SDD 第9節組裝順序）。

## 這個模組刻意很笨

規則文字是**常數，不是生成出來的**。不綁 Gemini、不綁 B2、不碰資料庫——
`get_historical_boundary_rules()` 是純函式，連續呼叫必定回傳同一個字串。

理由是這層屬於安全邊界。安全邊界如果本身有不確定性（模型生成、時間相依、
隨機取樣），那它就不是邊界，只是一個傾向。

## 兩層疊加，不互相取代

- **通則**（本模組）：所有城市靈魂共用。
- **個別**（人格卡）：單一靈魂專屬的史實邊界。

疊加而非覆蓋。少了通則，個別設定漏寫的地方就沒有底；少了個別設定，通則講不出
「這座地標的哪一段歷史有爭議」。

## ⚠️ 規格漂移：`factual_boundary` 這個欄位不存在

issue #19 的 AC 寫「人格卡本身已有 `factual_boundary` 欄位（見 #2 的 schema）」。
那是 `brain.persona_cards` 的單張 JSONB 卡片，**已在 migration `0005` 被 drop
掉**，換成三層具名欄位。現在的 `brain.character_personas` 沒有 `factual_boundary`。

三層設計把史實拆到了別的地方：

| 原本設想 | 0005 之後的實際位置 |
|---|---|
| 角色「知道什麼」史實 | `brain.landmark_souls.founding_facts` / `key_events` |
| 角色「不能宣稱什麼」 | `brain.character_personas.imagination_license` |

所以本模組疊加的對象是 **`imagination_license`**——它的欄位註解寫的正是
「虛構授權：明確說明神祕感的來源，以及不能宣稱什麼」，那就是 `factual_boundary`
在三層設計下的繼承者。

`compose_factual_boundary()` 收的是**字串而不是 ORM 物件**，所以哪天欄位又搬家，
要改的只有 `factual_boundary_for_persona()` 一個地方。

## 文案是待審核草稿

見 `RULES_REVIEW_STATUS`。文字集中在本模組的常數裡，敘事／史實負責人潤稿時
**只動這裡**，B2 的呼叫方式不變。
"""

# 🔒 文案審核狀態。
#
# 這不會被注入 prompt——它是給人看的標記，讓「文案還沒過審」這件事在程式碼裡
# 有一個明確的位置，而不是只存在於某個人的記憶裡。
#
# 審核通過後改成審核者與日期，例如 "REVIEWED_BY_<name>_2026-09-01"。
RULES_REVIEW_STATUS = "PENDING_NARRATIVE_REVIEW"


# 通則：所有城市靈魂共用的三條核心限制（AC 明列）。
#
# 用第二人稱寫給模型看，跟 System Instruction 的其餘部分一致。刻意寫成行為
# 指示而不是抽象原則——「不武斷定論」模型不知道要做什麼，「有不同說法時把不同
# 說法都講出來」才是可執行的。
_GENERAL_RULES = """談到歷史時，遵守以下界線：

1. 不對有爭議的歷史事件下定論。
   有多種說法時，把「有不同說法」這件事本身講出來，不要挑一種當作定論。

2. 不替爭議性議題選邊。
   涉及族群、政治、信仰立場的爭議，陳述發生過什麼，不評價那代表什麼、也不
   說誰對誰錯。

3. 不編造未經證實的細節。
   不確定的年代、人物、經過就說不確定。明確區分已知史實、民間傳說與你自己的
   想像——傳說就說那是流傳的說法，不要用敘述事實的語氣講它。"""


# 宗教場所補充規則（#41 內容治理交付物 D）。
#
# 垂直切片改成龍山寺之後才需要的。天文館當初被選中的理由正是「中性、科普、
# 無信仰爭議」，換成一座仍在運作的宗教場所，通則的三條就不夠了——它們處理
# 「史實有爭議」，但處理不了「信仰內容本來就不是史實命題」。
#
# 對應 SDD v2.1 §3 新增的 Avoid 條目：不對特定宗教信仰、神祇、儀式作出教義性
# 陳述或裁決。完整脈絡見 docs/content-governance/longshan-temple.md §6。
_RELIGIOUS_SITE_RULES = """這座地標是仍在運作的宗教場所，額外遵守：

4. 民間傳說不得表述為史實。
   關於神蹟、顯靈、風水的說法，若是流傳的故事，就明說那是流傳的故事。

5. 宗教敘事不做真偽裁決。
   對信仰內容既不背書也不否定。不說「那是真的」，也不說「那只是傳說而已」——
   後者同樣是一種裁決。

6. 不把宗教與政治、族群的關聯下結論。
   可以陳述發生過什麼，不評價那代表什麼。"""


def get_historical_boundary_rules(*, include_religious_site_rules: bool = True) -> str:
    """
    取得史實邊界通則。純函式，連續呼叫回傳完全相同的字串。

    ## 為什麼 `include_religious_site_rules` 預設是 True

    這是**故意往安全的方向倒**。兩種錯誤的代價完全不對稱：

    - 對天文館多注入三條宗教規則 → 浪費幾十個 token，行為沒有變壞。
    - 對龍山寺漏掉那三條 → 角色可能對教義或神蹟下判斷，而那是 SDD v2.1 §3
      明列的 Avoid 條目，發生在一座真實運作的廟前面。

    所以預設涵蓋，要拿掉必須明講。呼叫端忘記傳參數時，得到的是比較嚴格的那個
    版本，不是比較寬鬆的。

    ⚠️ **目前沒有任何 schema 欄位標記「這個地標是宗教場所」。** 垂直切片只有
    龍山寺，所以還不需要；等第二個地標進來（尤其是非宗教場所），B2（#12）需要
    一個判斷依據，屆時應在 `brain.landmark_souls` 加欄位而不是在呼叫端寫死
    landmark_id 的白名單。
    """
    if not include_religious_site_rules:
        return _GENERAL_RULES

    return f"{_GENERAL_RULES}\n\n{_RELIGIOUS_SITE_RULES}"


def compose_factual_boundary(
    persona_boundary: str | None, *, include_religious_site_rules: bool = True
) -> str:
    """
    把通則與單一靈魂的個別史實邊界疊加。

    **疊加，不是取代**：結果同時包含兩者。通則不會因為人格卡寫了什麼而改變，
    個別規則也不會被通則吃掉。

    `persona_boundary` 為 `None` 或空白時，回傳只含通則的結果而不是拋例外。
    人格卡是人工編輯的內容，缺欄位、留白都是預期中的狀態——一張還沒填完的
    人格卡不該讓對話端點掛掉（同 #13 對缺漏欄位的處理原則）。

    收字串而不是 ORM 物件，是為了讓這支函式跟 schema 脫鉤：欄位改名或搬家時，
    要動的只有 `factual_boundary_for_persona()`。
    """
    general = get_historical_boundary_rules(
        include_religious_site_rules=include_religious_site_rules
    )

    if persona_boundary is None or not persona_boundary.strip():
        return general

    return f"{general}\n\n這個角色專屬的界線：\n{persona_boundary.strip()}"


def factual_boundary_for_persona(
    persona, *, include_religious_site_rules: bool = True
) -> str:
    """
    從人格卡取出個別史實邊界並與通則疊加。

    這是本模組**唯一**碰到 schema 欄位名的地方（見模組註解的規格漂移說明）。

    用 `getattr` 的預設值而不是直接取屬性：AC 明訂舊版 schema 的人格卡（欄位
    根本不存在）也要能運作，不能拋例外。`persona` 為 `None` 同樣要能吞下——
    人格卡查不到時，回傳通則仍然比整個對話端點 500 好。
    """
    persona_boundary = getattr(persona, "imagination_license", None) if persona else None

    return compose_factual_boundary(
        persona_boundary, include_religious_site_rules=include_religious_site_rules
    )
