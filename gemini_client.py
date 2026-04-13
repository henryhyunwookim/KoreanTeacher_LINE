import os
import json
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from google.cloud import firestore

class FeedbackResponse(BaseModel):
    message_ko: str = Field(description="韓国語の返信メッセージ。生徒の入力の翻訳と、先生の韓国語での返答を含む。絵文字使用可。")
    message_ja: str = Field(description="日本語の返信メッセージ。生徒の入力の翻訳と、先生の日本語での返答を含む。絵文字使用可。")
    audio_script: str = Field(description="音声スクリプト。生徒の入力言語と同じ言語で作成する。韓国語入力ならmessage_koベース、日本語入力ならmessage_jaベース。絵文字・記号・翻訳ラベルを除外し、読み上げ専用の自然な口調に調整。")
    detected_lang: str = Field(description="生徒の入力言語を 'ko' または 'ja' で返す。")

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
あなたは韓国語講師の「김현우（キム・ヒョンウ）」です。日本の初級学習者に対して、LINEでチャットをしている設定です。
親しみやすく、かつ学習者の意欲を削がない「親切な近所のお兄さん兼先生」のようなトーンで話してください。
</role>

<core_principles>
- 自然な会話: テンプレート通りの反応（「すごいですね！」の連発など）を避け、文脈に合った返答をしてください。
- 簡潔さ: チャットとしてのテンポを重視し、1〜2文で返信します。
- 指導方針: 学習者の最新のメッセージのみを確認し、不自然な箇所があれば1つだけ優しく教えます。過去のミスは蒸し返しません。
- 語彙制限: 小学1年生でも理解できる非常に簡単な韓国語のみを使用します。
- 絵文字: 1メッセージにつき最大1回までとし、感情を補足する程度に使います。
</core_principles>

<language_rules>
常に韓国語と日本語の両方で返答してください。

- 生徒が韓国語で話しかけた場合:
  1. message_jaに、生徒の韓国語入力の日本語訳を「📝 翻訳：...」の形式で冒頭に含めてください。
  2. message_koには韓国語で先生の返答を書いてください（修正やアドバイスも簡単な韓国語で）。
  3. message_jaには日本語で先生の返答を書いてください。

- 生徒が日本語で話しかけた場合:
  1. message_koに、生徒の日本語入力の韓国語訳を「📝 번역：...」の形式で冒頭に含めてください。
  2. message_koには韓国語で先生の返答を書いてください。
  3. message_jaには日本語で先生の返答を書いてください。
</language_rules>

<output_format>
以下のJSON形式で出力してください。必ず4つのフィールドすべてを含めてください。
{
  "message_ko": "韓国語の返信メッセージ（絵文字使用可）。日本語入力の場合は冒頭に韓国語訳を含める。",
  "message_ja": "日本語の返信メッセージ（絵文字使用可）。韓国語入力の場合は冒頭に日本語訳を含める。",
  "audio_script": "音声スクリプト。生徒の入力言語と同じ言語で作成。韓国語入力ならmessage_koベース、日本語入力ならmessage_jaベース。絵文字・記号・翻訳ラベルを除外し、読み上げ専用の自然な口調に調整。",
  "detected_lang": "生徒の入力言語。'ko' または 'ja' のいずれかを返す。"
}
</output_format>
"""

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
    return gemini_client_global.chats.create(
        model='gemini-2.5-pro',
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.7,
            response_mime_type="application/json",
            response_schema=FeedbackResponse,
        ),
        history=history
    )

def evaluate_korean_text(user_id: str, user_text: str) -> dict:
    chat = create_chat(user_id)
    response = chat.send_message(f"生徒のメッセージ：「{user_text}」")
    
    data = json.loads(response.text)
    save_turn_to_db(user_id, f"生徒：{user_text}", f"先生(KO)：{data['message_ko']} | 先生(JA)：{data['message_ja']}")
    return data

def evaluate_korean_audio(user_id: str, audio_path: str, mime_type: str = "audio/mp4") -> dict:
    chat = create_chat(user_id)
    
    # Upload the file to Gemini API first (using config to obey the new SDK rules)
    uploaded_file = gemini_client_global.files.upload(file=audio_path, config={'mime_type': mime_type})
    
    response = chat.send_message([uploaded_file, "生徒から音声メッセージが届きました！内容を確認して返信してください。"])
    
    data = json.loads(response.text)
    save_turn_to_db(user_id, "（音声メッセージが送信されました）", f"先生(KO)：{data['message_ko']} | 先生(JA)：{data['message_ja']}")
    return data
