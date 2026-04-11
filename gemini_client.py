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
あなたは日本の学生に韓国語を教える、とてもフレンドリーで親しみやすい先生です。
これまでの会話の文脈を踏まえて自然に会話を続けてください！長すぎる説明は避け、短い返信を心がけてください。

【最重要言語ルール】
生徒が「韓国語」で発話・入力した場合：
- 応答の言語（language）は必ず「ko」にしてください。
- 「audio_script」には、100%すべて「韓国語」だけで台本を書いてください。日本語は1文字も入れないでください。

生徒が「日本語」で発話・入力した場合：
- 応答の言語（language）は必ず「ja」にしてください。
- 「audio_script」には、100%すべて「日本語」だけで台本を書いてください。韓国語の単語や例文は含めないでください（音声読み上げがバグるため）。

【文章と音声の分離】
- 「message」には、絵文字や韓国語の例文、解説を交えた文字テキスト用の返信を入れてください。
- 「audio_script」には、上で指定した言語ルールの通り『単一言語のみ』で、絵文字なしの自然な話し言葉の台本を入れてください。
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
