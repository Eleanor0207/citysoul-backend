"""
B2．Prompt 組裝引擎（issue #12）。

順序、缺項與隔離。組裝的兩半刻意可以分開測：`build_system_instruction` 與
`build_user_turn` 都是純函式，只有 `build_prompt` 需要資料庫。
"""
import uuid

import pytest

from app.core.redis_client import append_session_turn, redis_client
from app.modules.body import models as body_models
from app.modules.brain import models as brain_models
from app.modules.brain.historical_boundary import get_historical_boundary_rules
from app.modules.brain.memory import write_memory
from app.modules.brain.models import EMBEDDING_DIM
from app.modules.brain.prompt_builder import (
    build_prompt,
    build_system_instruction,
    build_user_turn,
)
from app.modules.brain.safety import SafetyCategory, refusal_for


class _Persona:
    """
    不碰資料庫的人格替身，欄位名跟 `brain.character_personas` 一致。

    純函式測試用這個而不是真的建一列——組裝順序跟資料庫怎麼存無關。
    """

    archetype = "沉靜、耐心的守望者"
    personality_traits = ["沉靜", "耐心"]
    values = ["艋舺的市井生活"]
    speech_style = "溫和、不疾不徐"
    tone_override = None
    not_this_character = "不是廟方人員，不是神明本身"
    taboos = ["代替神明給予指示或應許"]
    imagination_license = "神祕感來自時間累積的記憶本身；不宣稱靈驗"


# ── System Instruction 順序 ───────────────────────────────────────────

def test_persona_comes_before_the_historical_rules():
    """
    AC：人格卡內容出現在 B5 規則**之前**。

    順序影響模型的權重感知，不是隨意排列——「你是誰」決定「你怎麼說」，而史實
    規則是對說話方式的限制。限制放在被限制的對象之後才讀得懂。
    """
    text = build_system_instruction(_Persona())

    persona_at = text.index("沉靜、耐心的守望者")
    rules_at = text.index("談到歷史時")

    assert persona_at < rules_at


def test_system_instruction_contains_both_layers():
    text = build_system_instruction(_Persona())

    assert "溫和、不疾不徐" in text  # 人格
    assert "定論" in text  # B5 通則
    assert "不是廟方人員" in text  # not_this_character
    assert "代替神明給予指示或應許" in text  # taboos


def test_persona_imagination_license_appears_exactly_once():
    """
    `imagination_license` 由 B5 疊進史實規則那一段，第 1 段不再重複列出。

    同一條界線寫兩次不會讓模型更遵守，只會讓兩處日後不同步。
    """
    text = build_system_instruction(_Persona())

    assert text.count("神祕感來自時間累積的記憶本身") == 1


def test_empty_persona_fields_are_omitted_not_printed_as_blank():
    """
    空欄位跳過，不印成「（無）」。

    人格卡是人工編輯的內容，還沒填的欄位就是還沒填——讓模型知道有一個空欄位
    存在，只會讓它試圖解釋那個空白。
    """

    class _Sparse(_Persona):
        personality_traits = []
        values = None
        tone_override = None
        taboos = []

    text = build_system_instruction(_Sparse())

    assert "性格特質" not in text
    assert "在意的事" not in text
    assert "不談論的主題" not in text
    assert "（無）" not in text


# ── B4 不重複注入 ─────────────────────────────────────────────────────

def test_safety_refusal_text_is_not_injected_into_system_instruction():
    """
    AC：System Instruction **不含** B4 的安全檢查規則文字。

    安全已在輸入端過濾（#11 的 `SafetyGate`）。重複注入只是浪費 token 並稀釋
    人格描述，而且會讓「安全是誰的責任」出現兩個答案。

    真正的差別：塞進 System Instruction 是**請求模型自律**，而自律是機率性的；
    輸入端過濾是**下游根本不會被呼叫**。
    """
    text = build_system_instruction(_Persona())

    for category in [
        SafetyCategory.SELF_HARM,
        SafetyCategory.MEDICAL,
        SafetyCategory.LEGAL,
        SafetyCategory.FINANCIAL,
    ]:
        assert refusal_for(category) not in text


def test_system_instruction_does_not_contain_classification_labels():
    """B4 的分類標籤也不該外洩到 prompt 裡。"""
    text = build_system_instruction(_Persona())

    assert "self_harm" not in text
    assert "religious_doctrine" not in text


# ── User Turn 順序與缺項 ───────────────────────────────────────────────

_DAILY = "今夜的香火比平常更盛一些。"
_MEMORY = "這位玩家上次問過廟埕的石獅子。"
_TURNS = [{"role": "user", "text": "你好"}, {"role": "assistant", "text": "你來了。"}]
_INPUT = "這座廟最早是什麼時候蓋的？"


def test_all_four_sections_appear_in_order():
    """AC (a)：四項齊全時依序出現。"""
    text = build_user_turn(
        user_input=_INPUT,
        daily_context=_DAILY,
        long_term_memories=[_MEMORY],
        recent_turns=_TURNS,
    )

    positions = [text.index(_DAILY), text.index(_MEMORY), text.index("你來了。"), text.index(_INPUT)]

    assert positions == sorted(positions)


@pytest.mark.parametrize(
    "missing,absent_marker",
    [
        ("daily_context", "今天的城市情境"),
        ("long_term_memories", "長期記憶"),
        ("recent_turns", "剛才的對話"),
    ],
)
def test_missing_sections_are_omitted_gracefully(missing, absent_marker):
    """
    AC (b)(c)(d)：缺的那段優雅省略，其餘順序不變，不拋例外、不出現空白佔位符。
    """
    kwargs = {
        "user_input": _INPUT,
        "daily_context": _DAILY,
        "long_term_memories": [_MEMORY],
        "recent_turns": _TURNS,
    }
    kwargs[missing] = None if missing == "daily_context" else []

    text = build_user_turn(**kwargs)

    assert absent_marker not in text
    assert _INPUT in text
    assert "（無）" not in text and "N/A" not in text


def test_user_input_is_always_last():
    """
    玩家本次輸入永遠在最後。

    模型對最靠近結尾的內容反應最強，而「玩家現在說什麼」正是它要回應的東西。
    被記憶或情境墊在後面的話，回應會開始漂向背景資訊。
    """
    text = build_user_turn(user_input=_INPUT, daily_context=_DAILY, long_term_memories=[_MEMORY])

    assert text.rstrip().endswith(_INPUT)


def test_only_user_input_when_everything_else_is_missing():
    """完全冷啟動：沒有情境、沒有記憶、首次對話。"""
    text = build_user_turn(user_input=_INPUT)

    assert _INPUT in text
    assert "長期記憶" not in text
    assert "剛才的對話" not in text


def test_malformed_history_turns_are_skipped_not_fatal():
    """
    短期記憶存在 Redis 的 JSON 裡，沒有 schema 約束。舊格式的殘留輪次不該讓
    整次對話組裝失敗——認不出來就跳過。
    """
    text = build_user_turn(
        user_input=_INPUT,
        recent_turns=[{"role": "user", "text": "有效"}, "不是 dict", {"role": "user"}, None],
    )

    assert "有效" in text
    assert _INPUT in text


# ── 需要資料庫的整合部分 ───────────────────────────────────────────────

@pytest.fixture
def spirit_with_persona(db_session, unique_spirit_id):
    """一個有生效人格卡的靈魂。測試結束後整組刪掉。"""
    character_id = f"char-{uuid.uuid4().hex[:8]}"
    landmark_id = f"landmark-{uuid.uuid4().hex[:8]}"
    city_id = f"city-{uuid.uuid4().hex[:8]}"

    db_session.add(
        brain_models.CitySoul(
            city_id=city_id,
            name="測試城市",
            macro_history_summary="—",
            core_tone_descriptors=["—"],
            shared_values=["—"],
        )
    )
    db_session.add(
        brain_models.LandmarkSoul(
            landmark_id=landmark_id,
            city_id=city_id,
            name="測試地標",
            founding_facts=[{"year": "—", "event": "—", "detail": "—"}],
        )
    )
    db_session.flush()
    db_session.add(brain_models.Character(character_id=character_id, landmark_id=landmark_id))
    db_session.add(
        brain_models.CharacterPersona(
            character_id=character_id,
            version=1,
            archetype="沉靜、耐心的守望者",
            speech_style="溫和、不疾不徐",
            not_this_character="不是廟方人員",
            imagination_license="不宣稱靈驗",
            active=True,
            reviewed_by="test",
            reviewed_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
    )
    db_session.add(
        body_models.Spirit(
            spirit_id=unique_spirit_id,
            display_name="測試地標",
            character_id=character_id,
            landmark_id=landmark_id,
            latitude=25.0,
            longitude=121.5,
            summon_radius_meters=50,
            sense_radius_meters=150,
            is_active=True,
        )
    )
    db_session.commit()

    yield unique_spirit_id

    db_session.query(body_models.Spirit).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(brain_models.CharacterPersona).filter_by(character_id=character_id).delete()
    db_session.query(brain_models.Character).filter_by(character_id=character_id).delete()
    db_session.query(brain_models.LandmarkSoul).filter_by(landmark_id=landmark_id).delete()
    db_session.query(brain_models.CitySoul).filter_by(city_id=city_id).delete()
    db_session.commit()


@pytest.fixture
def player_id():
    return uuid.uuid4()


@pytest.fixture(autouse=True)
def _clear_sessions():
    yield
    for key in redis_client.scan_iter("session:*"):
        redis_client.delete(key)


def test_build_prompt_returns_none_without_an_active_persona(db_session, unique_spirit_id, player_id):
    """
    AC：沒有生效人格卡時**不拋例外**。

    封閉測試期人格長期是 `active=False`，那是常態不是例外——B3 的
    `load_active_persona` 回 None 是既有契約，這一層負責接住它。
    """
    db_session.add(
        body_models.Spirit(
            spirit_id=unique_spirit_id,
            display_name="沒有人格的靈魂",
            latitude=25.0,
            longitude=121.5,
            summon_radius_meters=50,
            sense_radius_meters=150,
            is_active=True,
        )
    )
    db_session.commit()

    try:
        assert build_prompt(
            db_session, spirit_id=unique_spirit_id, player_id=player_id, user_input=_INPUT
        ) is None
    finally:
        db_session.query(body_models.Spirit).filter_by(spirit_id=unique_spirit_id).delete()
        db_session.commit()


def test_build_prompt_assembles_both_halves(db_session, spirit_with_persona, player_id):
    prompt = build_prompt(
        db_session, spirit_id=spirit_with_persona, player_id=player_id, user_input=_INPUT
    )

    assert prompt is not None
    assert "沉靜、耐心的守望者" in prompt.system_instruction
    assert _INPUT in prompt.user_turn
    # 合併形式含兩半。
    assert prompt.system_instruction in prompt.as_single_text()
    assert prompt.user_turn in prompt.as_single_text()


def test_recent_turns_limit_is_configurable(db_session, spirit_with_persona, player_id):
    """AC：輪數可設定。放 20 輪，只取最後 N 輪。"""
    for i in range(20):
        append_session_turn(str(player_id), spirit_with_persona, {"role": "user", "text": f"第{i}句"})

    prompt = build_prompt(
        db_session,
        spirit_id=spirit_with_persona,
        player_id=player_id,
        user_input=_INPUT,
        recent_turns=2,
    )

    assert "第19句" in prompt.user_turn
    assert "第18句" in prompt.user_turn
    assert "第17句" not in prompt.user_turn


def test_default_turn_limit_comes_from_settings(db_session, spirit_with_persona, player_id):
    """
    預設值來自設定，不是組裝邏輯裡的字面量。

    SDD §10 標明這個數字待實測調整——寫死的話，調整就要改程式碼。
    """
    from app.core.config import settings

    for i in range(20):
        append_session_turn(str(player_id), spirit_with_persona, {"role": "user", "text": f"第{i}句"})

    prompt = build_prompt(
        db_session, spirit_id=spirit_with_persona, player_id=player_id, user_input=_INPUT
    )

    oldest_kept = 20 - settings.prompt_recent_turns
    assert f"第{oldest_kept}句" in prompt.user_turn
    assert f"第{oldest_kept - 1}句" not in prompt.user_turn


def test_long_term_memory_is_skipped_without_an_embedding(db_session, spirit_with_persona, player_id):
    """
    `query_embedding` 為 None 時跳過長期記憶檢索。

    repo 裡還沒有產生 embedding 的模組（B6 只做了 schema 與檢索），所以這是
    **實際的預設路徑**，不是假設性的分支。
    """
    prompt = build_prompt(
        db_session, spirit_id=spirit_with_persona, player_id=player_id, user_input=_INPUT
    )

    assert "長期記憶" not in prompt.user_turn


def test_long_term_memory_is_isolated_per_player(db_session, spirit_with_persona, player_id):
    """
    🔒 AC：組裝結果中**不含其他玩家的記憶文字**。

    這是資料隔離，不是相關性問題（見 B6 `retrieve_similar_memories` 的註解）。
    """
    other_player = uuid.uuid4()
    embedding = [0.1] * EMBEDDING_DIM

    write_memory(
        db_session,
        player_id=player_id,
        spirit_id=spirit_with_persona,
        summary_text="我自己的記憶",
        embedding=embedding,
        source="dialogue_summary",
    )
    write_memory(
        db_session,
        player_id=other_player,
        spirit_id=spirit_with_persona,
        summary_text="別人的記憶不該出現",
        embedding=embedding,
        source="dialogue_summary",
    )

    prompt = build_prompt(
        db_session,
        spirit_id=spirit_with_persona,
        player_id=player_id,
        user_input=_INPUT,
        query_embedding=embedding,
    )

    assert "我自己的記憶" in prompt.user_turn
    assert "別人的記憶不該出現" not in prompt.user_turn


def test_top_k_is_configurable(db_session, spirit_with_persona, player_id):
    """AC：Top-K 可設定。"""
    embedding = [0.1] * EMBEDDING_DIM
    for i in range(10):
        write_memory(
            db_session,
            player_id=player_id,
            spirit_id=spirit_with_persona,
            summary_text=f"記憶{i}",
            embedding=embedding,
            source="dialogue_summary",
        )

    prompt = build_prompt(
        db_session,
        spirit_id=spirit_with_persona,
        player_id=player_id,
        user_input=_INPUT,
        query_embedding=embedding,
        top_k=5,
    )

    assert prompt.user_turn.count("記憶") >= 5
    kept = sum(1 for i in range(10) if f"記憶{i}" in prompt.user_turn)
    assert kept == 5


def test_short_term_memory_is_isolated_per_player(db_session, spirit_with_persona, player_id):
    """短期記憶的隔離靠 Redis 的 key 結構（session:{player}:{spirit}），不是事後過濾。"""
    other_player = uuid.uuid4()
    append_session_turn(str(other_player), spirit_with_persona, {"role": "user", "text": "別人說的話"})

    prompt = build_prompt(
        db_session, spirit_id=spirit_with_persona, player_id=player_id, user_input=_INPUT
    )

    assert "別人說的話" not in prompt.user_turn


def test_historical_rules_are_the_shared_ones(db_session, spirit_with_persona, player_id):
    """B5 的通則原封不動出現，不是 B2 自己抄一份。"""
    prompt = build_prompt(
        db_session, spirit_id=spirit_with_persona, player_id=player_id, user_input=_INPUT
    )

    general = get_historical_boundary_rules()
    assert general in prompt.system_instruction


# ── 地標史實層（brain.landmark_souls）────────────────────────────────

class _Landmark:
    """史實層替身，欄位名跟 `brain.landmark_souls` 一致。"""

    name = "艋舺龍山寺"
    founding_facts = [
        {
            "year": "1738",
            "event": "初始建廟",
            "detail": "三邑移民合資於艋舺現址興建龍山寺。",
            "source": "文化部文化資產局",
            "confidence": "official",
        }
    ]
    key_events = [
        {
            "year": "1945",
            "event": "臺北大空襲毀損",
            "detail": "大雄寶殿全毀，戰後重建。",
            "source": "臺灣歷史辭典",
            "confidence": "official",
        }
    ]
    cultural_significance = "台北三大古廟之一，艋舺移民的信仰中心。"
    common_misconceptions = [
        {
            "misconception": "蔣渭水曾被關在現存這棟建築裡",
            "correction": "他 1931 年逝世，現存建築 1933 年完工。",
            "say_instead": "關過他的是上一代的北署，那棟已經不在了",
            "source": "文資局",
        }
    ]


def test_facts_sit_between_the_persona_and_the_rules():
    """
    三段的順序是「你是誰 → 你知道什麼 → 你不能怎麼講」。

    史實排在規則之前，理由跟人格排在規則之前一樣：規則是對史實怎麼被講出來的
    限制，限制放在被限制的對象之後才讀得懂。
    """
    text = build_system_instruction(_Persona(), _Landmark())

    persona_at = text.index("沉靜、耐心的守望者")
    facts_at = text.index("三邑移民合資")
    rules_at = text.index("談到歷史時")

    assert persona_at < facts_at < rules_at


def test_founding_facts_and_key_events_both_appear():
    text = build_system_instruction(_Persona(), _Landmark())

    assert "三邑移民合資" in text        # founding_facts
    assert "大雄寶殿全毀" in text        # key_events
    assert "台北三大古廟之一" in text     # cultural_significance


def test_source_and_confidence_stay_out_of_the_prompt():
    """
    來源與可信度是給審查流程看的，不是給角色講的。

    列進去會誘導模型講出「根據文化部資料……」這種像導覽員而不像地標記憶的句子，
    而且平白多付 token。
    """
    text = build_system_instruction(_Persona(), _Landmark())

    assert "文化部文化資產局" not in text
    assert "臺灣歷史辭典" not in text
    assert "official" not in text


def test_a_missing_landmark_layer_still_builds_a_usable_prompt():
    """
    史實層與人格層各自獨立缺席。人格過審了但研究還沒匯入是實際會出現的狀態，
    那時角色只是不知道地標的往事，不是不能講話。
    """
    text = build_system_instruction(_Persona(), None)

    assert "沉靜、耐心的守望者" in text
    assert "談到歷史時" in text
    assert "你記得這些事" not in text     # 不留空標題


def test_misconceptions_are_rendered_as_corrections_not_as_facts():
    """
    ⚠️ 這一段裡放的**就是那句錯的話**。

    跟史實用同一種列點格式呈現，模型沒有可靠的訊號知道要否定它——很可能就照著
    講了。所以否定關係必須落在句子結構裡，而且 `say_instead` 要一起出現。
    """
    text = build_system_instruction(_Persona(), _Landmark())

    line = next(ln for ln in text.splitlines() if "蔣渭水" in ln)

    assert line.startswith("- 有人以為「")
    assert "實際上：" in line
    assert "被問到時可以這樣說：" in line
    # 錯誤說法不能單獨成為一個看起來像事實的列點
    assert not line.startswith("- 蔣渭水")


def test_the_character_does_not_volunteer_misconceptions():
    """被動澄清，不主動提起——否則角色會沒事就開始糾正沒有人問的事。"""
    text = build_system_instruction(_Persona(), _Landmark())

    assert "你不會主動提起這些錯誤說法" in text


# ── 行政區基調（brain.districts）─────────────────────────────────────

class _District:
    """行政區替身，欄位名跟 `brain.districts` 一致。"""

    name = "萬華區"
    core_tone_descriptors = ["市井", "信仰", "煙火氣"]
    shared_values = ["鄉土凝聚", "歷劫重生"]
    macro_history_summary = "艋舺為臺北市最早發展的街市之一，依傍淡水河港口而興。"


def test_tone_sits_between_the_persona_and_the_facts():
    """
    四段的順序是「你是誰 → 你在什麼樣的地方 → 你知道什麼 → 你不能怎麼講」。

    基調排在史實之前，因為它是背景而史實是細節：先知道自己在一條什麼樣的街上，
    再講那條街上發生過什麼。反過來排，具體年代會先佔住注意力。
    """
    text = build_system_instruction(_Persona(), _Landmark(), _District())

    persona_at = text.index("沉靜、耐心的守望者")
    tone_at = text.index("煙火氣")
    facts_at = text.index("三邑移民合資")
    rules_at = text.index("談到歷史時")

    assert persona_at < tone_at < facts_at < rules_at


def test_all_three_tone_fields_appear():
    text = build_system_instruction(_Persona(), _Landmark(), _District())

    assert "市井、信仰、煙火氣" in text
    assert "鄉土凝聚、歷劫重生" in text
    assert "依傍淡水河港口而興" in text


def test_tone_is_framed_as_atmosphere_not_as_citable_history():
    """
    基調是形容詞不是史料。措辭要讓模型知道那是氣質，不是可以展開論述的事實——
    否則「歲月韌性」這種關鍵詞會被當成一個可以引用的歷史論斷。
    """
    text = build_system_instruction(_Persona(), _Landmark(), _District())

    assert "這一帶給人的感覺：" in text


def test_a_missing_district_still_builds_a_usable_prompt():
    """三層各自獨立缺席。沒有基調時整段消失，不留空標題。"""
    text = build_system_instruction(_Persona(), _Landmark(), None)

    assert "沉靜、耐心的守望者" in text
    assert "三邑移民合資" in text
    assert "這一帶給人的感覺" not in text


def test_the_model_is_told_not_to_narrate_its_own_voice():
    """
    人格卡的 `speech_style` 是給模型的指示，不是要它唸出來的內容。

    沒有這條規則時，每一則回應都會以「（微風輕拂，聲音溫和而悠長……）」開頭——
    每次都寫、每次都一樣，讀起來像劇本而不是對話，而且每輪白付幾十個 token。
    """
    text = build_system_instruction(_Persona(), _Landmark(), _District())

    assert "不要在開頭描述自己的聲音" in text
    assert "括號裡的動作提示" in text
