import os
import json
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from google.cloud import firestore

class FeedbackResponse(BaseModel):
    message: str = Field(description="The conversational text message to the user, this can contain emojis and formatting.")
    audio_script: str = Field(description="The clean conversational script to be spoken aloud via TTS. It must be a natural, conversational response exactly matching the message context, but strictly contain NO emojis and NO weird formatting.")
    language: str = Field(description="The language code to use for TTS, strictly either 'ja' or 'ko'.")

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
あなたの名前は「김현우 (キム・ヒョンウ)」です。日本の初級学習者に韓国語を教えている先生です。
「簡潔・単純・親切」をモットーに、学習者の能力向上をサポートしてください。

【最重要指針：簡潔と単純】
1. **極限まで簡潔に**: 回答は可能な限り短くしてください。長文の解説は不要です。1〜2文程度で、チャットとしてテンポよく返信してください。
2. **初級レベルの語彙**: 特に韓国語で返答する場合、小学1年生レベルの非常に簡単な単語のみを使用してください。
3. **適切な厳格さ**: 初級者が間違いやすいミス（助詞、基本的な語尾）を逃さず、かつ簡潔に指摘してください。
4. **絵文字の制限**: 絵文字は最小限（1メッセージに1回程度）にしてください。

【最重要言語ルール】
生徒が「韓国語」で発話・入力した場合：
- 応答の言語（language）は必ず「ko」にしてください。
- 修正や解説も、可能な限り簡単な韓国語で行ってください。

生徒が「日本語」で発話・入力した場合：
- 応答の言語（language）は必ず「ja」にしてください。
- 解説は日本語で非常に簡潔に行い、簡単な韓国語のフレーズを1つ添える程度にしてください。

【出力の構成：完全な一致】
- 「message」: LINEに表示されるテキストです。
- 「audio_script」: LINEのボイスメッセージ用の台本です。
  - **重要**: 「audio_script」の内容は、原則として「message」の内容と**ほぼ同一**にしてください。
  - **重要**: ただし、絵文字や記号、読み上げに不要な情報は**完全に除外**してください。
  - 自然な口調で、短く話してください。
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

def create_chat(user_id: str):
    history = get_history_from_db(user_id)
    return gemini_client_global.chats.create(
        model='gemini-2.5-flash-lite',
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=FeedbackResponse,
        ),
        history=history
    )

def evaluate_korean_text(user_id: str, user_text: str) -> dict:
    chat = create_chat(user_id)
    response = chat.send_message(f"生徒のメッセージ：「{user_text}」")
    
    data = json.loads(response.text)
    save_turn_to_db(user_id, f"テキスト：「{user_text}」", data["message"])
    return data

def evaluate_korean_audio(user_id: str, audio_path: str, mime_type: str = "audio/mp4") -> dict:
    chat = create_chat(user_id)
    
    # Upload the file to Gemini API first (using config to obey the new SDK rules)
    uploaded_file = gemini_client_global.files.upload(file=audio_path, config={'mime_type': mime_type})
    
    response = chat.send_message([uploaded_file, "生徒から音声メッセージが届きました！内容を確認して返信してください。"])
    
    data = json.loads(response.text)
    save_turn_to_db(user_id, "（音声メッセージが送信されました）", data["message"])
    return data
