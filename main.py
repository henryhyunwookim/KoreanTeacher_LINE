import os
import tempfile
import subprocess
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    AudioMessage,
    MessagingApiBlob
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    AudioMessageContent
)
from dotenv import load_dotenv
from google.cloud import texttospeech

import gemini_client

load_dotenv()

app = FastAPI()

# Validate that the necessary environment variables are set
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
BASE_URL = os.environ.get("BASE_URL", "https://korean-teacher-bot-438254561187.asia-northeast1.run.app")

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
    print("Warning: LINE_CHANNEL_SECRET or LINE_CHANNEL_ACCESS_TOKEN is not set.")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.get("/audio/{filename}")
def get_audio(filename: str):
    file_path = f"/tmp/{filename}"
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="audio/mp4")
    raise HTTPException(status_code=404, detail="File not found")

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    body_str = body.decode("utf-8")
    
    try:
        handler.handle(body_str, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_id = event.source.user_id
    user_text = event.message.text
    try:
        # Get feedback from Gemini (Structured JSON)
        feedback_data = gemini_client.evaluate_korean_text(user_id, user_text)
        feedback_msg = feedback_data["message"]
        
        # Send reply back to user
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=feedback_msg)]
                )
            )
    except Exception as e:
        print(f"Error processing text: {e}")
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="申し訳ありません。エラーが発生しました。(Gemini Error)")]
                )
            )

@handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio_message(event):
    user_id = event.source.user_id
    message_id = event.message.id
    
    # Download the audio file
    temp_file_path = ""
    with ApiClient(configuration) as api_client:
        line_bot_blob_api = MessagingApiBlob(api_client)
        audio_content = line_bot_blob_api.get_message_content(message_id)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".m4a") as tp:
            tp.write(audio_content)
            temp_file_path = tp.name

    try:
        # Get feedback from Gemini (Structured JSON)
        feedback_data = gemini_client.evaluate_korean_audio(user_id, temp_file_path, mime_type="audio/mp4")
        feedback_msg = feedback_data["message"]
        feedback_script = feedback_data["audio_script"]
        feedback_lang = feedback_data["language"]
        
        # 1. Generate text-to-speech mp3 using Google Cloud TTS
        mp3_path = f"/tmp/{message_id}_out.mp3"
        m4a_path = f"/tmp/{message_id}_out.m4a"
        
        tts_client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text=feedback_script)
        
        if feedback_lang == 'ko':
            voice = texttospeech.VoiceSelectionParams(
                language_code="ko-KR",
                name="ko-KR-Neural2-a"
            )
        else:
            voice = texttospeech.VoiceSelectionParams(
                language_code="ja-JP",
                name="ja-JP-Neural2-B"
            )

        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3
        )
        
        tts_response = tts_client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )
        
        with open(mp3_path, "wb") as out:
            out.write(tts_response.audio_content)
        
        # 2. Convert to m4a using ffmpeg
        subprocess.run(["ffmpeg", "-y", "-i", mp3_path, "-c:a", "aac", "-b:a", "64k", m4a_path], check=True)
        
        # 3. Get audio duration for LINE API
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", m4a_path],
            capture_output=True, text=True, check=True
        )
        duration_ms = int(float(result.stdout.strip()) * 1000)
        
        # 4. Construct Public URL
        audio_url = f"{BASE_URL}/audio/{message_id}_out.m4a"
        
        # Send reply back to user (ONLY Audio as requested)
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[
                        AudioMessage(original_content_url=audio_url, duration=duration_ms)
                    ]
                )
            )
    except Exception as e:
        print(f"Error processing audio: {e}")
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="申し訳ありません。音声の処理中にエラーが発生しました。")]
                )
            )
    finally:
        # Clean up the input temporary file
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        # Note: We do not delete mp3_path or m4a_path here immediately because LINE servers need to download it!
        # Cloud Run ephemeral file system cleans itself upon instance termination.

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
