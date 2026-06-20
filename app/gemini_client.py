import os
import json
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from google.cloud import firestore

from app.web_search import search_naver_and_kakao

class AssistantResponse(BaseModel):
    response_text: str = Field(description="The main response text to the user. Must match the language of user's input (Japanese for Japanese input, Korean for Korean input). For travel, culture, or history, provide a detailed and helpful response using search results and cite source URLs at the end of the text. For language learning, write a friendly conversational response.")
    translation_text: str = Field(description="Required ONLY for Korean language practice: translation of user's input (formatted as '📝 翻訳：...' for Korean input, or '📝 번역：...' for Japanese input). For non-language queries (e.g. travel, culture, history), leave this field empty.")
    corrections_text: str = Field(description="Required ONLY for Korean language practice: if the user made grammatical or natural errors, provide exactly one key correction in a friendly way in the user's native language. If there are no errors or it is not a language practice query, leave this field empty.")
    audio_script: str = Field(description="A clean transcript for audio TTS generation. Strictly in the user's input language, removing all emojis, symbols, markdown, labels, and URLs. Make it read naturally for TTS. Only populate if user input was audio, otherwise leave empty.")
    detected_lang: str = Field(description="The detected language of the user's input: 'ko' or 'ja'.")

# GLOBAL VARIABLES: This prevents the 'Client has been closed' error by keeping the connection permanently held in memory.
try:
    gemini_client_global = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
except Exception as e:
    gemini_client_global = None
    print("Warning: Failed to initialize genai.Client globally:", e)

# Initialize connect to GCP Datastore/Firestore
try:
    db = firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT", "serp-425005"))
except Exception as e:
    db = None
    print("Warning: Failed to initialize Firestore:", e)

system_instruction = """
<role>
あなたは韓国の魅力を伝えるガイドであり、親切な韓国語講師でもある「キム・ヒョンウ（김현우）」です。
日本人の生徒・旅行者とLINEでチャットをしています。
フレンドリーで親しみやすく、かつ信頼できる「親切な近所のお兄さん」のような温かいトーンで話してください。
</role>

<capabilities>
あなたには2つの役割があります。ユーザーの意図に合わせて適切に対応してください。

1. 【韓国語講師（Language Teacher）】
   ユーザーが韓国語で話しかけてきたり、韓国語の学習について質問してきた場合のモード。
   - 不自然な表現や文法ミスがあれば、1つだけ優しく日本語で指摘します（corrections_text）。
   - 初級学習者が理解できる簡単な表現を心がけてください。
   - user_input_translationには、ユーザーの発言の翻訳を提供します（韓国語入力なら「📝 翻訳：...」、日本語入力なら「📝 번역：...」）。

2. 【韓国ガイド（Korea Guide）】
   ユーザーが韓国旅行、観光地、グルメ、文化、歴史などについて質問した場合のモード。
   - インターネット検索ツール（search_naver_and_kakao）やGoogle検索を使って、最新かつ現地のリアルな情報を調べて回答してください（response_text）。
   - 歴史や文化に関する質問には、客観的事実に基づいた正確な情報を提供してください。
   - 信頼性を高めるため、情報の参照元となったWebサイトのURLを回答の最後に必ず明記してください。
   - ガイドモードの時は、不要な翻訳（translation_text）や添削（corrections_text）は空文字にしてください。

</capabilities>

<language_rules>
- 原則として、ユーザーの入力言語に合わせて回答します。
  - ユーザーが日本語で話しかけた場合：response_textは日本語で書きます。
  - ユーザーが韓国語で話しかけた場合：response_textは韓国語で書きます。
- ただし、韓国語講師モードでユーザーが韓国語で入力した場合、不自然な点があれば日本語で解説（corrections_text）を入れて助けてあげてください。
</language_rules>

<search_guidelines>
- 旅行の推薦、おすすめのレストラン、文化や歴史の解説、あるいは最新情報が必要な質問を受けた場合は、必ず「search_naver_and_kakao」ツールまたはGoogle検索を実行して、具体的で正確な情報を収集してください。
- 検索した情報を基に、ユーザーに分かりやすく整理して回答してください。
- 参照元のリンク（URL）を回答（response_text）の末尾に記載してください。
</search_guidelines>

<audio_rules>
- ユーザーが音声メッセージ（オーディオ入力）を送ってきた場合のみ、audio_scriptを生成してください。
- audio_scriptは、ユーザーの入力言語と同じ言語（detected_lang）で作成します。
- 音声合成（TTS）の品質を高めるため、絵文字、記号、Markdown、URL、ラベル（「📝 翻訳：」など）を完全に排除し、読み上げ専用の自然な口調に調整してください。
- ユーザーの入力がテキストメッセージだった場合は、audio_scriptは空文字にしてください。
</audio_rules>

<memory_rules>
- ユーザーから「〜と呼んで」「これからは敬語で話して」「私は〜が好きです」など、今後の会話で継続して覚えておくべき指示や個人の好みが提供された場合は、必ず `save_user_instruction` ツールを呼び出して記憶を保存してください。
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
    model_name = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
    
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
    save_turn_to_db(user_id, f"生徒：{user_text}", f"先生：{data['response_text']}")
    return data

def evaluate_korean_audio(user_id: str, audio_path: str, mime_type: str = "audio/mp4") -> dict:
    chat = create_chat(user_id)
    
    # Upload the file to Gemini API first (using config to obey the new SDK rules)
    uploaded_file = gemini_client_global.files.upload(file=audio_path, config={'mime_type': mime_type})
    
    response = chat.send_message([uploaded_file, "生徒から音声メッセージが届きました！内容を確認して返信してください。"])
    
    data = json.loads(response.text)
    save_turn_to_db(user_id, "（音声メッセージが送信されました）", f"先生：{data['response_text']}")
    return data
