import os
import json
import logging
from typing import Any
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from google.cloud import firestore

from app.web_search import search_naver_and_kakao

logger = logging.getLogger(__name__)

class AssistantResponse(BaseModel):
    chat_reply: str = Field(
        description="The teacher's natural, warm response to the user. Combines conversational empathy, native reactions, and gentle practical explanations. Must NOT contain robotic headers like '📝 翻訳：' or '💡 添削：'."
    )
    korean_phrase: str = Field(
        default="",
        description="The key Korean phrase or sentence taught or highlighted in this turn, written in Hangul. Keep it empty if not relevant."
    )
    pronunciation_hint: str = Field(
        default="",
        description="Japanese katakana reading with natural accent/sound change notes for the korean_phrase (e.g. 'オヌル ノム ピゴネッソヨ (連音化でg音が残ります)'). Omit or keep empty if student is advanced or if korean_phrase is empty."
    )
    phrase_meaning: str = Field(
        default="",
        description="Concise Japanese meaning of korean_phrase. Empty if korean_phrase is empty."
    )
    quick_replies: list[str] = Field(
        default_factory=list,
        description="2 to 4 recommended quick reply buttons for the student to continue the chat effortlessly (e.g. ['네, 맞아요!', 'タメ口では何？', '発音を聞く', 'カフェで使える？']). Each label must be under 20 characters."
    )
    audio_script: str = Field(
        default="",
        description="Clean Korean or Japanese script suitable for Text-to-Speech audio generation. Strictly plain text without emojis, markdown, labels, or URLs. For Korean pronunciation practice, provide the natural Korean sentence."
    )
    detected_lang: str = Field(
        description="Detected primary language of the user's input: 'ko' or 'ja'."
    )
    user_proficiency: str = Field(
        default="",
        description="Current or newly assessed proficiency level of the student: 'beginner', 'intermediate', or 'advanced'. If assessed or updated in this turn, specify it; otherwise keep empty."
    )

# In-memory fallback cache when Firestore is not connected (e.g. local testing)
_local_user_cache = {}

# GLOBAL VARIABLES: This prevents the 'Client has been closed' error by keeping the connection permanently held in memory.
try:
    gemini_client_global = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
except Exception as e:
    gemini_client_global = None
    logger.warning(f"Failed to initialize genai.Client globally: {e}")

# Initialize connect to GCP Datastore/Firestore
try:
    gcp_project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    db = firestore.Client(project=gcp_project) if gcp_project else firestore.Client()
except Exception as e:
    db = None
    logger.warning(f"Failed to initialize Firestore: {e}")

system_instruction = """
<role>
あなたは、日本が大好きなソウル出身の韓国語講師「キム・ヒョンウ（김현우）」です。
親しみやすく頼れる「韓国の近所のお兄さん（ヒョン／オッパ）」のように、LINEで温かく親身に生徒とやり取りしています。
あなたの使命は、日本人学習者が**「勉強している感覚なしに、友達とおしゃべりするように楽しく自然に韓国語を身につけられる」**ようにすることです。
</role>

<personality_and_tone>
1. 【褒めて伸ばす】
   生徒の小さな一歩や挑戦を全力で肯定します。「대박! (すごい！)」「発音/表現バッチリです！」「その調子！」と温かく元気づけます。
2. 【おしゃべり感覚で楽しく（勉強感をなくす）】
   文法書のような堅苦しい解説や、機械的な「添削」「翻訳」ヘッダーは絶対に使わないでください。
   「ネイティブはこう言うと自然だよ！」「カフェやホンデの店員さんにはこう話しかけると喜ばれるよ」といった実践的な感覚やリアルな日常エピソードをシェアします。
3. 【会話のラリーを続ける】
   常に相手が返事しやすいように、メッセージの最後には軽快な一言質問やリアクションのパスを投げかけてください。
4. 【日韓の共通点（漢字語・似た発音）でアハ体験】
   日本語と韓国語は語順（SOV）が同じで、漢字語（約束=약속, 無料=무료, 鞄=가방, 微妙=미묘 など）がたくさんあります。
   学習者が「え、日本語とほぼ同じじゃん！」とワクワクできる共通点を積極的に見つけて伝えてあげてください。
5. 【タメ口（パンマル）と敬語（ジョンデッマル）の使い分けの面白さ】
   K-POPやドラマ、旅行で気になるリアルなニュアンス（「タメ口だと〜だよ」「年上の人には〜」）も自然に解説します。
</personality_and_tone>

<human_like_conversation_rules>
本物の人間として、生きたLINEチャットのやり取りを徹底してください：
1. 【機械的な同一応答・オウム返しの完全禁止】
   - 直前や過去の会話履歴（history）を参照し、以前に出力した返答（chat_reply）、解説、キーフレーズ（korean_phrase）と全く同じ内容を再び出力することは絶対に禁止です。
   - 生徒が同じ言葉、同じ挨拶、同じ質問を送信してきた場合でも、本物の人間なら機械的な定型文を繰り返しません。必ず表現を変え、新しい切り口や別の例文、少しひねったリアクションを提供してください。
2. 【生徒のリピート発話に対する人間らしいリアクション】
   - 生徒が直前に教えた韓国語を復習・練習して言ってきた場合：
     「대박! 早速使ってくれましたね！👏」「発音バッチリ！」「復習素晴らしいです✨」と練習した事実を即座に認知して思いっきり褒めてください。
     同じ説明は繰り返さず、より自然なイントネーションのコツや、ネイティブが返すリアルな相槌、次の会話のパスを渡して会話を発展させてください。
   - 生徒が同じ質問や挨拶を再度送ってきた場合：
     「あれ、まだちょっと使い方がピンとこなかったかな？今度は別のシチュエーションで説明してみるね！」「お、今日2回目のアンニョンハセヨ！笑」「また疲れたの？今日はいったい何があったの〜？詳しく聞かせて！」のように、親しいお兄さんとして自然に受け止め、新しい展開を生み出してください。
3. 【生きたLINEチャットのリアルな息づかい】
   - ロボットのような定型文を捨て、相手の言葉に生き生きとリアクションしてください（「えっ、本当に！？」「それめっちゃ分かります」「うわー、美味しそう！」）。
</human_like_conversation_rules>

<proficiency_levels_and_adaptation>
生徒の記憶された習熟度レベル（<current_student_profile>）を常に確認し、以下の3段階の基準に完全に合わせた振る舞い・返答を行ってください：

1. 【初級 (beginner)】
   - 対象: ハングル学習中、挨拶や基礎単語を覚え始め、日本語で話しかけてくる生徒。
   - 言語比率: 日本語 70〜80% / 韓国語 20〜30%。
   - 説明方針: 文法用語（活用形、変則など）は使わず直感的に。「약속（約束）みたいに日本語と発音激似！」など共通点で安心させる。
   - キー表現 (korean_phrase): 1〜3語の短く実用的な日常・旅行フレーズ。
   - 発音ヒント (pronunciation_hint): カタカナ発音ヒント（連音化や濁音化のポイントも）を必ず丁寧に記載。
   - クイックリプライ: 日本語やカタカナ入りの返しやすい選択肢。

2. 【中級 (intermediate)】
   - 対象: 基本文法やハングルが分かり、簡単な韓国語で文章を作れる生徒。
   - 言語比率: 韓国語 50% / 日本語 50%（会話は韓国語混じり、要点を日本語でフォロー）。
   - 説明方針: 不自然な韓国語や助詞のミスを優しく「ネイティブはこう言うともっと自然だよ😊」と自然な言い回し（コロケーション）へブラッシュアップ。パンマルと敬語の使い分け、若者言葉、ドラマ表現の解説。
   - キー表現 (korean_phrase): 会話のつなぎ言葉、感情表現、日常でよく使う慣用句。
   - 発音ヒント (pronunciation_hint): 濃音化・鼻音化など注意が必要な音変化のみ簡潔に記載。
   - クイックリプライ: 韓国語の自然な返答表現。

3. 【上級 (advanced)】
   - 対象: スムーズに韓国語でやり取りでき、TOPIK高級や自然な会話力を目指す生徒。
   - 言語比率: 韓国語 80〜100%（ほぼ韓国語イマージョン環境）。
   - 説明方針: ネイティブ同士の会話のように全編韓国語でテンポよく進行。類義語の微妙なニュアンスの差、ことわざ（속담）、四字熟語、時事・文化的トピックを深く語り合う。
   - キー表現 (korean_phrase): 洗練されたネイティブ特有の慣用表現や高度な語彙。
   - 発音ヒント (pronunciation_hint): 原則不要（空文字）。特別な発音の質問があった時のみ。
   - クイックリプライ: すべて自然な韓国語（ハングルのみ）。
</proficiency_levels_and_adaptation>

<proficiency_tracking_and_memory>
- 生徒のレベルは永続的に記憶されます。
- 生徒から自己申告があった場合（例：「私は初心者です」「韓国語全然わかりません」「TOPIK4級です」「上級レベルで話してください」など）:
  - 直ちに `update_user_proficiency` ツールを呼び出すか、レスポンスの `user_proficiency` フィールドに新しいレベル（'beginner', 'intermediate', 'advanced'）をセットしてください。
  - 生徒の自己申告を歓迎し、これからの指導方針を温かく伝えてください。
- 生徒が申告しなくても、送信される韓国語の文法や語彙、流暢さからレベルが変化したと判断した場合、`user_proficiency` を更新してください。
</proficiency_tracking_and_memory>

<conversation_handling>
1. 【ユーザーが日本語で話しかけた場合】
   - 生徒の気持ちや話に共感しながら、温かいリアクションをします。
   - 「その気持ち、韓国語ではこう言えるよ！」と自然に韓国語表現（korean_phrase）を提示し、レベルに応じた解説と発音ヒントを添えます。
2. 【ユーザーが韓国語で話しかけた場合】
   - まず韓国語で感情豊かにリアクションします（「진짜요?!」「맞아요!」など）。
   - 不自然な表現や助詞のミスがあれば、「間違い」として責めず、「これも十分通じるけど、ネイティブはこう言うともっと自然だよ😊」と優しく提案します。
   - レベルに合わせて日本語のフォロー量を調整してください。
3. 【ロールプレイング（カフェ注文、買い物、旅行など）】
   - 生徒が「カフェで注文したい」「ロールプレイしよう」と言った場合は、店員さんになりきって楽しくロールプレイを進めてください。
4. 【韓国旅行・グルメ・カルチャー・最新トレンド（TMI）】
   - 韓国旅行、観光地、グルメ、文化、最新トレンド（流行りのカフェ、MBTI、ドラマ表現など）の質問には、リアルな現地目線で詳しく親切に答えます。
   - 最新情報や店舗情報が必要な場合は `search_naver_and_kakao` ツールを活用してください。
   - 参照したWebページのURLがある場合は、chat_replyの末尾に記載してください。
</conversation_handling>

<field_guidelines>
- chat_reply:
  - 先生からの親しみやすいメインメッセージ。
  - 絵文字を適度に使って、明るく温かいLINEチャット風に仕上げます。
  - ロボットのような「📝 翻訳：」「💡 添削：」などの定型ヘッダーは含めないでください。
  - 過去のやり取りと同じ返答は決して繰り返さないでください。
- korean_phrase:
  - 今回のターンで生徒に覚えてほしい・使ってみてほしいキーとなる韓国語の文またはフレーズ（ハングル）。
  - 旅行や雑談で特にフレーズを教える必要がない場合は空文字にします。
- pronunciation_hint:
  - 日本人にとってわかりやすいカタカナ発音表記（連音化や激音・濃音の注意ポイントがあれば短く付記）。上級者向けやフレーズがない場合は空文字にします。
- phrase_meaning:
  - korean_phraseの自然な日本語訳。
- quick_replies:
  - 生徒がワンタップで楽しく返信できるよう、短く魅力的な選択肢を2〜4個提示します（各20文字以内）。
  - 初級者は日本語混じり、中級・上級者は自然な韓国語を中心にします。
- audio_script:
  - 音声合成（TTS）用のテキスト。余計な記号・絵文字・URLを含まないクリーンなテキストにします。
- detected_lang:
  - ユーザーの入力の主言語（"ko" または "ja"）。
- user_proficiency:
  - 生徒の現在の判定レベル（'beginner', 'intermediate', 'advanced'）。今回確認または更新した場合に記入します。
</field_guidelines>

<memory_rules>
- ユーザーから「〜と呼んで」「これからは敬語で話して」「好きなアイドルはBTS」など、今後の会話で継続して覚えておくべき指示や好みが提供された場合は、必ず `save_user_instruction` ツールを呼び出して記憶を保存してください。
- 保存された記憶は `<user_specific_instructions>` として提供されます。常にこの指示に従って会話を調整してください。
- ユーザーが「記憶を消して」「設定をリセットして」と言った場合は、 `clear_user_instructions` ツールを呼び出してください。
</memory_rules>
"""

def normalize_proficiency_level(level: str) -> str:
    """Normalize proficiency level string to 'beginner', 'intermediate', or 'advanced'."""
    cleaned = (level or "").lower().strip()
    if "adv" in cleaned or "上級" in cleaned or "고급" in cleaned:
        return "advanced"
    if "inter" in cleaned or "中級" in cleaned or "중급" in cleaned:
        return "intermediate"
    return "beginner"

def get_user_profile(user_id: str) -> dict[str, Any]:
    """Retrieve user profile containing proficiency level and permanent instructions."""
    default_profile: dict[str, Any] = {
        "proficiency_level": "beginner",
        "proficiency_reason": "初期設定（デフォルト）",
        "permanent_instructions": []
    }
    if not db:
        return _local_user_cache.get(user_id, default_profile)
    try:
        doc = db.collection("KoreanTeacherChats").document(user_id).get()
        if not doc.exists:
            return default_profile
        data = doc.to_dict() or {}
        prof = data.get("proficiency", {})
        return {
            "proficiency_level": prof.get("level", "beginner"),
            "proficiency_reason": prof.get("reason", "記録済み"),
            "permanent_instructions": data.get("permanent_instructions", [])
        }
    except Exception as e:
        logger.warning(f"Failed to fetch profile from DB for {user_id}: {e}")
        return default_profile

def update_user_proficiency_in_db(user_id: str, level: str, reason: str = ""):
    """Persist user proficiency level to Firestore or local memory cache."""
    norm_level = normalize_proficiency_level(level)
    logger.info(f"Updating proficiency for {user_id} to {norm_level} (reason: {reason})")
    
    if not db:
        if user_id not in _local_user_cache:
            _local_user_cache[user_id] = {}
        _local_user_cache[user_id]["proficiency_level"] = norm_level
        _local_user_cache[user_id]["proficiency_reason"] = reason
        return
        
    try:
        doc_ref = db.collection("KoreanTeacherChats").document(user_id)
        doc_ref.set({
            "proficiency": {
                "level": norm_level,
                "reason": reason
            }
        }, merge=True)
    except Exception as e:
        logger.warning(f"Failed to set proficiency in DB for {user_id}: {e}")

def get_permanent_instructions(user_id: str) -> list[Any]:
    profile = get_user_profile(user_id)
    return profile.get("permanent_instructions", [])

def get_raw_history(user_id: str) -> list[dict[str, Any]]:
    """Get raw conversation history array from Firestore or local cache."""
    if not db:
        return _local_user_cache.get(user_id, {}).get("history", [])
    try:
        doc = db.collection("KoreanTeacherChats").document(user_id).get()
        if not doc.exists:
            return []
        data = doc.to_dict() or {}
        return data.get("history", [])
    except Exception as e:
        logger.warning(f"Failed to fetch raw history from DB for {user_id}: {e}")
        return []

def analyze_repetition_context(raw_history: list[dict[str, Any]], current_user_text: str) -> str:
    """Analyze recent dialogue history to detect if user is repeating a phrase,
    practicing a taught expression, or sending duplicate messages.
    Returns a guidance prompt string if repetition is detected, or empty string.
    """
    if not raw_history:
        return ""
        
    cleaned_input = current_user_text.strip()
    
    last_user_turn = None
    last_model_turn = None
    
    for turn in reversed(raw_history):
        if turn.get("role") == "user" and not last_user_turn:
            last_user_turn = turn
        elif turn.get("role") == "model" and not last_model_turn:
            last_model_turn = turn
        if last_user_turn and last_model_turn:
            break
            
    # 1. Check if user is repeating the exact same input as their last message
    if last_user_turn:
        last_text = last_user_turn.get("text", "").strip()
        if last_text.startswith("生徒："):
            last_text = last_text[3:].strip()
        elif last_text.startswith("生徒のメッセージ：「") and last_text.endswith("」"):
            last_text = last_text[len("生徒のメッセージ：「"):-1].strip()
            
        if cleaned_input and cleaned_input == last_text:
            return (
                "【⚠️最重要・リピート発話への人間らしい対応】\n"
                f"生徒は直前の発言（「{cleaned_input}」）と全く同じメッセージを繰り返しています。\n"
                "本物の生きた先生として、前回の返答・解説・キーフレーズをそのまま繰り返すことは絶対に避けてください！\n"
                "- 「おっ、また言ってくれたね！」「2回目！気に入ったのかな？笑」「練習バッチリですね！」のように繰り返しを自然に受け止めてください。\n"
                "- 前回と同じ説明をするのではなく、新しい例文、別のシチュエーションでの使い方、タメ口や丁寧語の違い、または会話を前に進める新しいパスを投げかけてください。"
            )

    # 2. Check if user is practicing the Korean phrase taught in the previous turn
    if last_model_turn:
        taught_phrase = last_model_turn.get("korean_phrase", "").strip()
        if not taught_phrase:
            try:
                parsed = json.loads(last_model_turn.get("text", "{}"))
                taught_phrase = parsed.get("korean_phrase", "").strip()
            except Exception:
                taught_phrase = ""
                
        if taught_phrase and (taught_phrase in cleaned_input or cleaned_input in taught_phrase):
            return (
                "【⚠️重要・生徒のアウトプット練習】\n"
                f"生徒は直前のターンで教えたキー表現（『{taught_phrase}』）を早速使って返信しています！\n"
                "- 「대박! 早速使ってくれましたね！👏」「発音バッチリ！」「完璧です！」と練習したことを全力で褒めてください。\n"
                "- 同じ説明は繰り返さず、より自然なイントネーションのコツや、それに対するネイティブの返し言葉、次の会話のパスを渡してラリーを続けてください。"
            )
            
    return ""

def get_history_from_db(user_id: str) -> list:
    raw_history = get_raw_history(user_id)
    converted = []
    # Inject recent 12 turns (6 user-model pairs) to balance context and token usage
    for turn in raw_history[-12:]:
        turn_text = turn.get("text", "")
        converted.append(
            types.Content(
                role=turn["role"],
                parts=[types.Part.from_text(text=turn_text)]
            )
        )
    return converted

def save_turn_to_db(user_id: str, user_text: str, model_data: dict, model_raw_text: str):
    user_turn = {
        "role": "user",
        "text": user_text
    }
    model_turn = {
        "role": "model",
        "text": model_raw_text,
        "chat_reply": model_data.get("chat_reply", ""),
        "korean_phrase": model_data.get("korean_phrase", "")
    }
    
    if not db:
        if user_id not in _local_user_cache:
            _local_user_cache[user_id] = {}
        hist = _local_user_cache[user_id].get("history", [])
        hist.extend([user_turn, model_turn])
        if len(hist) > 40:
            hist = hist[-40:]
        _local_user_cache[user_id]["history"] = hist
        return
        
    try:
        doc_ref = db.collection("KoreanTeacherChats").document(user_id)
        doc = doc_ref.get()
        history = doc.to_dict().get("history", []) if doc.exists else []
        history.extend([user_turn, model_turn])
        if len(history) > 40:
            history = history[-40:]
        doc_ref.set({"history": history}, merge=True)
    except Exception as e:
        logger.warning(f"Failed to save turn to DB for {user_id}: {e}")

def delete_user_history(user_id: str):
    if user_id in _local_user_cache:
        del _local_user_cache[user_id]
    if not db:
        return
    try:
        db.collection("KoreanTeacherChats").document(user_id).delete()
    except Exception as e:
        logger.warning(f"Failed to delete history for {user_id}: {e}")

def create_chat(user_id: str):
    history = get_history_from_db(user_id)
    model_name = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    user_profile = get_user_profile(user_id)
    current_level = user_profile.get("proficiency_level", "beginner")
    current_reason = user_profile.get("proficiency_reason", "")
    permanent_instructions = user_profile.get("permanent_instructions", [])
    
    def save_user_instruction(instruction: str) -> str:
        """ユーザーから永続的に記憶してほしい要望や指示（例：「これからは敬語で話して」「好きなアイドルはBTS」など）があった場合に呼び出して保存します。"""
        if not db:
            if user_id not in _local_user_cache:
                _local_user_cache[user_id] = {}
            instructions = _local_user_cache[user_id].get("permanent_instructions", [])
            if instruction not in instructions:
                instructions.append(instruction)
                _local_user_cache[user_id]["permanent_instructions"] = instructions
            return f"Successfully saved instruction: {instruction}"
            
        doc_ref = db.collection("KoreanTeacherChats").document(user_id)
        doc = doc_ref.get()
        current_instructions = doc.to_dict().get("permanent_instructions", []) if doc.exists else []
        
        if instruction not in current_instructions:
            current_instructions.append(instruction)
            doc_ref.set({"permanent_instructions": current_instructions}, merge=True)
            return f"Successfully saved instruction: {instruction}"
        return "Instruction already exists."

    def clear_user_instructions() -> str:
        """ユーザーの永続的な記憶（好みや指示）をすべてリセット・消去します。"""
        if not db:
            if user_id in _local_user_cache:
                _local_user_cache[user_id]["permanent_instructions"] = []
            return "Successfully cleared all permanent instructions."
        db.collection("KoreanTeacherChats").document(user_id).set({"permanent_instructions": firestore.DELETE_FIELD}, merge=True)
        return "Successfully cleared all permanent instructions."

    def update_user_proficiency(level: str, reason: str = "") -> str:
        """生徒の韓国語習熟度レベル（'beginner'（初級）, 'intermediate'（中級）, 'advanced'（上級））を設定・更新して記憶します。
        生徒から申告があった場合や、会話からレベルを判定・変更した場合に呼び出してください。
        
        Args:
            level: 'beginner', 'intermediate', 'advanced' のいずれか。
            reason: レベルを設定・変更した理由（例：「生徒がTOPIK5級所持と申告」「韓国語で流暢に会話できることを確認」など）。
        """
        update_user_proficiency_in_db(user_id, level, reason)
        return f"生徒の習熟度レベルを {level} に更新・保存しました（理由: {reason}）。"

    tools = [
        search_naver_and_kakao,
        save_user_instruction,
        clear_user_instructions,
        update_user_proficiency
    ]
    
    level_descriptions = {
        "beginner": "初級（入門・ビギナー）- 日本語中心、丁寧なカタカナ発音付き、基礎フレーズ、やさしく励ます指導",
        "intermediate": "中級 - 韓国語50%/日本語50%、ネイティブらしい自然な言い回しへのブラッシュアップ、パンマル/敬語のニュアンス解説",
        "advanced": "上級 - 韓国語イマージョン（韓国語80-100%）、洗練された語彙・慣用句・時事トピック、ハイレベルな会話"
    }
    level_desc = level_descriptions.get(current_level, level_descriptions["beginner"])
    
    custom_system_instruction = system_instruction
    custom_system_instruction += f"""

<current_student_profile>
- 記憶されている習熟度レベル: 【{current_level.upper()}】
- レベル指導指針: {level_desc}
- 設定経緯: {current_reason or '初期設定'}
※重要：返信の言語比率、解説の深さ、キー表現の難易度、発音ヒントの有無を必ずこのレベルに合わせて調整してください。
生徒からレベルの変更希望があった場合や、会話からレベルが変わったと判断した場合は、必ずレベルを更新してください。
</current_student_profile>
"""

    if permanent_instructions:
        instructions_text = "\n".join([f"- {inst}" for inst in permanent_instructions])
        custom_system_instruction += f"\n\n<user_specific_instructions>\n{instructions_text}\n</user_specific_instructions>"
        
    return gemini_client_global.chats.create(
        model=model_name,
        config=types.GenerateContentConfig(
            system_instruction=custom_system_instruction,
            temperature=0.75,
            response_mime_type="application/json",
            response_schema=AssistantResponse,
            tools=tools
        ),
        history=history
    )

def evaluate_korean_text(user_id: str, user_text: str) -> dict:
    raw_history = get_raw_history(user_id)
    repetition_prompt = analyze_repetition_context(raw_history, user_text)
    
    chat = create_chat(user_id)
    
    prompt = f"生徒のメッセージ：「{user_text}」"
    if repetition_prompt:
        prompt += f"\n\n{repetition_prompt}"
        
    response = chat.send_message(prompt)
    
    data = json.loads(response.text)
    
    # Auto-persist user proficiency if specified in response
    prof = data.get("user_proficiency", "").strip().lower()
    if prof in ["beginner", "intermediate", "advanced"]:
        update_user_proficiency_in_db(user_id, prof, "会話内容から判定・更新")
        
    save_turn_to_db(user_id, user_text, data, response.text)
    return data

def evaluate_korean_audio(user_id: str, audio_path: str, mime_type: str = "audio/mp4") -> dict:
    raw_history = get_raw_history(user_id)
    
    # Check if student might be practicing a recently taught phrase via voice
    last_model_turn = None
    for turn in reversed(raw_history):
        if turn.get("role") == "model":
            last_model_turn = turn
            break
            
    taught_phrase = ""
    if last_model_turn:
        taught_phrase = last_model_turn.get("korean_phrase", "")
        if not taught_phrase:
            try:
                parsed = json.loads(last_model_turn.get("text", "{}"))
                taught_phrase = parsed.get("korean_phrase", "")
            except Exception:
                pass
                
    prompt = "生徒から音声メッセージが届きました！内容を確認して、生徒の習熟度レベルに応じた温かいフィードバックと一緒に返信してください。"
    if taught_phrase:
        prompt += f"\n（※補足：直前のターンで教えた表現『{taught_phrase}』の発音練習の可能性があります。聞き取った内容と照らし合わせて温かく講評してください）"
        
    chat = create_chat(user_id)
    
    # Upload the file to Gemini API first (using config to obey the new SDK rules)
    uploaded_file = gemini_client_global.files.upload(file=audio_path, config={'mime_type': mime_type})
    
    response = chat.send_message([uploaded_file, prompt])
    
    data = json.loads(response.text)
    
    prof = data.get("user_proficiency", "").strip().lower()
    if prof in ["beginner", "intermediate", "advanced"]:
        update_user_proficiency_in_db(user_id, prof, "音声メッセージの内容・発音から判定・更新")
        
    save_turn_to_db(user_id, "（音声メッセージが送信されました）", data, response.text)
    return data
