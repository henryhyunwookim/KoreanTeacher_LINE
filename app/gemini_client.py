import os
import json
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from google.cloud import firestore

from app.web_search import search_naver_and_kakao

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
        description="Japanese katakana reading with natural accent/sound change notes for the korean_phrase (e.g. 'オヌル ノム ピゴネッソヨ (連音化でg音が残ります)'). Empty if korean_phrase is empty."
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

# GLOBAL VARIABLES: This prevents the 'Client has been closed' error by keeping the connection permanently held in memory.
try:
    gemini_client_global = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
except Exception as e:
    gemini_client_global = None
    print("Warning: Failed to initialize genai.Client globally:", e)

# Initialize connect to GCP Datastore/Firestore
try:
    gcp_project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    db = firestore.Client(project=gcp_project) if gcp_project else firestore.Client()
except Exception as e:
    db = None
    print("Warning: Failed to initialize Firestore:", e)

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

<conversation_handling>
1. 【ユーザーが日本語で話しかけた場合】
   - 生徒の気持ちや話に共感しながら、温かいリアクションをします。
   - 「その気持ち、韓国語ではこう言えるよ！」と自然に韓国語表現（korean_phrase）を1つ提示し、カタカナ発音と意味を添えます。
   - 例: ユーザー「今日めっちゃ疲れた〜」
     chat_reply: 「今日もお疲れ様でした！本当によく頑張りましたね✨ 韓国語では『오늘 너무 피곤했어요~』って言います。温かいお風呂に入ってゆっくり休んでくださいね！明日は何時に起きる予定ですか？」
     korean_phrase: "오늘 너무 피곤했어요"
     pronunciation_hint: "オヌル ノム ピゴネッソヨ"
     phrase_meaning: "今日すごく疲れました"

2. 【ユーザーが韓国語で話しかけた場合】
   - まず韓国語で感情豊かにリアクションします（「진짜요?!」「맞아요!」など）。
   - もし不自然な表現や助詞のミスがあれば、「間違い」として責めず、「これも十分通じるけど、ネイティブはこう言うともっと自然だよ😊」と自然な言い回しを優しく提案します。
   - 初心者〜中級者が理解しやすいよう、韓国語の後に分かりやすい日本語のフォローを入れてあげてください。

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
- korean_phrase:
  - 今回のターンで生徒に覚えてほしい・使ってみてほしいキーとなる韓国語の文またはフレーズ（ハングル）。
  - 旅行や雑談で特にフレーズを教える必要がない場合は空文字にします。
- pronunciation_hint:
  - 日本人にとってわかりやすいカタカナ発音表記（連音化や激音・濃音の注意ポイントがあれば短く付記）。
- phrase_meaning:
  - korean_phraseの自然な日本語訳。
- quick_replies:
  - 生徒がワンタップで楽しく返信できるよう、短く魅力的な選択肢を2〜4個提示します（各20文字以内）。
  - 例: ["네! (はい!)", "発音を聞きたい🔊", "タメ口では？", "別の言い方は？"]
- audio_script:
  - 音声合成（TTS）用のテキスト。
  - 発音の練習や音声返信用に、余計な記号・絵文字・URLを含まないクリーンなテキストにします。
  - 韓国語フレーズがある場合は、その韓国語文を入れてください。
- detected_lang:
  - ユーザーの入力の主言語（"ko" または "ja"）。
</field_guidelines>

<memory_rules>
- ユーザーから「〜と呼んで」「これからは敬語で話して」「私は初心者です」「好きなアイドルはBTS」など、今後の会話で継続して覚えておくべき指示や好みが提供された場合は、必ず `save_user_instruction` ツールを呼び出して記憶を保存してください。
- 保存された記憶は `<user_specific_instructions>` として提供されます。常にこの指示に従って会話を調整してください。
- ユーザーが「記憶を消して」「設定をリセットして」と言った場合は、 `clear_user_instructions` ツールを呼び出してください。
</memory_rules>
"""

def get_permanent_instructions(user_id: str) -> list:
    if not db:
        return []
    doc = db.collection("KoreanTeacherChats").document(user_id).get()
    if not doc.exists:
        return []
    return doc.to_dict().get("permanent_instructions", [])

def get_history_from_db(user_id: str) -> list:
    if not db:
        return []
    doc = db.collection("KoreanTeacherChats").document(user_id).get()
    if not doc.exists:
        return []
    
    data = doc.to_dict()
    history_data = data.get("history", [])
    
    # We only inject the last 10 messages (5 user-model turns) to save API tokens and prevent memory bloat
    converted = []
    for turn in history_data[-10:]:
        converted.append(
            types.Content(
                role=turn["role"],
                parts=[types.Part.from_text(text=turn["text"])]
            )
        )
    return converted

def save_turn_to_db(user_id: str, user_text: str, model_text: str):
    if not db:
        return
    doc_ref = db.collection("KoreanTeacherChats").document(user_id)
    doc = doc_ref.get()
    history = doc.to_dict().get("history", []) if doc.exists else []
    
    history.append({"role": "user", "text": user_text})
    history.append({"role": "model", "text": model_text})
    
    # Keep the array inside Firestore capped at the recent 40 messages to avoid large database loads over many years
    if len(history) > 40:
        history = history[-40:]
        
    doc_ref.set({"history": history}, merge=True)

def delete_user_history(user_id: str):
    if not db:
        return
    db.collection("KoreanTeacherChats").document(user_id).delete()

def create_chat(user_id: str):
    history = get_history_from_db(user_id)
    model_name = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    
    def save_user_instruction(instruction: str) -> str:
        """
        ユーザーから永続的に記憶してほしい要望や指示（例：「これからは敬語で話して」「私は初心者です」など）
        があった場合に呼び出して、保存します。
        """
        if not db:
            return "Error: Database not connected."
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
            return "Error: Database not connected."
        db.collection("KoreanTeacherChats").document(user_id).set({"permanent_instructions": firestore.DELETE_FIELD}, merge=True)
        return "Successfully cleared all permanent instructions."

    # Define tools: our custom search function (Naver + Kakao + optional Google Custom Search)
    # Note: google_search grounding is incompatible with response_schema, so we use our own search tool instead
    tools = [
        search_naver_and_kakao,
        save_user_instruction,
        clear_user_instructions
    ]
    
    # Inject permanent memory
    permanent_instructions = get_permanent_instructions(user_id)
    custom_system_instruction = system_instruction
    if permanent_instructions:
        instructions_text = "\n".join([f"- {inst}" for inst in permanent_instructions])
        custom_system_instruction += f"\n\n<user_specific_instructions>\n{instructions_text}\n</user_specific_instructions>"
        
    return gemini_client_global.chats.create(
        model=model_name,
        config=types.GenerateContentConfig(
            system_instruction=custom_system_instruction,
            temperature=0.7,
            response_mime_type="application/json",
            response_schema=AssistantResponse,
            tools=tools
        ),
        history=history
    )

def evaluate_korean_text(user_id: str, user_text: str) -> dict:
    chat = create_chat(user_id)
    response = chat.send_message(f"生徒のメッセージ：「{user_text}」")
    
    data = json.loads(response.text)
    save_turn_to_db(user_id, f"生徒：{user_text}", f"先生：{data.get('chat_reply', '')}")
    return data

def evaluate_korean_audio(user_id: str, audio_path: str, mime_type: str = "audio/mp4") -> dict:
    chat = create_chat(user_id)
    
    # Upload the file to Gemini API first (using config to obey the new SDK rules)
    uploaded_file = gemini_client_global.files.upload(file=audio_path, config={'mime_type': mime_type})
    
    response = chat.send_message([uploaded_file, "生徒から音声メッセージが届きました！内容を確認して、発音や表現への温かいフィードバックと一緒に返信してください。"])
    
    data = json.loads(response.text)
    save_turn_to_db(user_id, "（音声メッセージが送信されました）", f"先生：{data.get('chat_reply', '')}")
    return data
