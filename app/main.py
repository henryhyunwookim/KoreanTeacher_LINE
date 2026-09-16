import os
import sys
import tempfile
import subprocess
import threading
import logging
import uuid
import traceback
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    ShowLoadingAnimationRequest,
    TextMessage,
    AudioMessage,
    QuickReply,
    QuickReplyItem,
    MessageAction,
    MessagingApiBlob
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    AudioMessageContent,
    UnfollowEvent,
    MemberLeftEvent,
    LeaveEvent
)
from dotenv import load_dotenv
from google.cloud import texttospeech

from app import gemini_client

load_dotenv()

# Configure structured logging for Cloud Run visibility
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    force=True
)
logger = logging.getLogger(__name__)

app = FastAPI()

# Validate that the necessary environment variables are set
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
    logger.warning("LINE_CHANNEL_SECRET or LINE_CHANNEL_ACCESS_TOKEN is not set.")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.get("/health")
def health_check():
    """Health check endpoint for diagnostics."""
    return {
        "status": "ok",
        "model": os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
        "base_url": BASE_URL,
        "has_naver": bool(os.environ.get("NAVER_CLIENT_ID")),
        "has_kakao": bool(os.environ.get("KAKAO_REST_API_KEY")),
    }

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

@handler.add(UnfollowEvent)
def handle_unfollow(event):
    user_id = event.source.user_id
    logger.info(f"User {user_id} unfollowed/blocked. Clearing history.")
    gemini_client.delete_user_history(user_id)

@handler.add(MemberLeftEvent)
def handle_member_left(event):
    for member in event.left.members:
        user_id = member.user_id
        if user_id:
            logger.info(f"User {user_id} left group/room. Clearing history.")
            gemini_client.delete_user_history(user_id)

@handler.add(LeaveEvent)
def handle_bot_leave(event):
    source_type = event.source.type
    if source_type == 'group':
        chat_id = event.source.group_id
    elif source_type == 'room':
        chat_id = event.source.room_id
    else:
        chat_id = event.source.user_id
    
    logger.info(f"Bot left {source_type} {chat_id}. Clearing history for this context.")
    gemini_client.delete_user_history(chat_id)

def generate_tts_audio(text: str, lang: str, output_id: str) -> tuple:
    """Generate TTS audio file and return (url, duration_ms).
    
    Args:
        text: The clean script text for TTS.
        lang: 'ko' or 'ja'.
        output_id: Unique identifier for the output file.
    
    Returns:
        Tuple of (audio_url, duration_ms).
    """
    mp3_path = f"/tmp/{output_id}_{lang}.mp3"
    m4a_path = f"/tmp/{output_id}_{lang}.m4a"
    
    tts_client = texttospeech.TextToSpeechClient()
    synthesis_input = texttospeech.SynthesisInput(text=text)
    
    if lang == 'ko':
        voice = texttospeech.VoiceSelectionParams(
            language_code="ko-KR",
            name="ko-KR-Neural2-C"
        )
    else:
        voice = texttospeech.VoiceSelectionParams(
            language_code="ja-JP",
            name="ja-JP-Neural2-D"
        )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3
    )
    
    tts_response = tts_client.synthesize_speech(
        input=synthesis_input, voice=voice, audio_config=audio_config
    )
    
    with open(mp3_path, "wb") as out:
        out.write(tts_response.audio_content)
    
    # Convert to m4a using ffmpeg
    subprocess.run(["ffmpeg", "-y", "-i", mp3_path, "-c:a", "aac", "-b:a", "64k", m4a_path], check=True)
    
    # Get audio duration for LINE API
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", m4a_path],
        capture_output=True, text=True, check=True
    )
    duration_ms = int(float(result.stdout.strip()) * 1000)
    
    audio_url = f"{BASE_URL}/audio/{output_id}_{lang}.m4a"
    return audio_url, duration_ms

def show_loading(user_id: str):
    """Show loading animation for 1-to-1 chat to signal the bot is working."""
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        show_loading_request = ShowLoadingAnimationRequest(
            chatId=user_id,
            loadingSeconds=20
        )
        line_bot_api.show_loading_animation(show_loading_request)

def download_audio_content(message_id: str) -> str:
    """Download audio message from LINE servers and save to temp file."""
    with ApiClient(configuration) as api_client:
        line_bot_blob_api = MessagingApiBlob(api_client)
        audio_content = line_bot_blob_api.get_message_content(message_id)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".m4a") as tp:
            tp.write(audio_content)
            return tp.name

def format_assistant_response(feedback_data: dict, is_audio: bool) -> list:
    """Format AssistantResponse into friendly LINE message objects with pronunciation hints and quick replies."""
    messages = []
    
    chat_reply = feedback_data.get("chat_reply", "").strip()
    korean_phrase = feedback_data.get("korean_phrase", "").strip()
    pronunciation_hint = feedback_data.get("pronunciation_hint", "").strip()
    phrase_meaning = feedback_data.get("phrase_meaning", "").strip()
    raw_quick_replies = feedback_data.get("quick_replies", [])
    
    # Fallback compatibility with previous schema if needed
    if not chat_reply:
        chat_reply = feedback_data.get("response_text", "").strip()
    
    transcript_parts = []
    if chat_reply:
        transcript_parts.append(chat_reply)
        
    # Append structured practical Korean phrase card if taught
    if korean_phrase:
        card_lines = [f"✨ 今日のキー表現：\n『 {korean_phrase} 』"]
        if pronunciation_hint:
            card_lines.append(f"🗣️ 発音：{pronunciation_hint}")
        if phrase_meaning:
            card_lines.append(f"🇯🇵 意味：{phrase_meaning}")
        transcript_parts.append("\n".join(card_lines))
        
    full_text = "\n\n".join(transcript_parts)
    
    # Build Quick Reply items (max 13 allowed by LINE, keep it to 2-4 clean options)
    quick_reply_items = []
    if raw_quick_replies and isinstance(raw_quick_replies, list):
        for qr in raw_quick_replies:
            qr_text = str(qr).strip()
            if qr_text:
                # LINE QuickReply text & label max is 20 characters
                label = qr_text[:20]
                quick_reply_items.append(
                    QuickReplyItem(
                        action=MessageAction(label=label, text=label)
                    )
                )
    
    # If student received a Korean phrase, offer a button to ask for pronunciation if not already present
    is_advanced = feedback_data.get("user_proficiency") == "advanced"
    has_pronunciation_btn = any("発音" in getattr(item.action, 'label', '') or "발음" in getattr(item.action, 'label', '') for item in quick_reply_items)
    if korean_phrase and not has_pronunciation_btn:
        if len(quick_reply_items) < 4:
            if is_advanced:
                quick_reply_items.append(
                    QuickReplyItem(
                        action=MessageAction(label="발음 듣기🔊", text=f"『{korean_phrase[:12]}』 발음 들려줘!")
                    )
                )
            else:
                quick_reply_items.append(
                    QuickReplyItem(
                        action=MessageAction(label="発音を聞かせて🔊", text=f"『{korean_phrase[:12]}』の発音を聞かせて！")
                    )
                )

    quick_reply = QuickReply(items=quick_reply_items) if quick_reply_items else None
    
    if full_text:
        messages.append(TextMessage(text=full_text, quick_reply=quick_reply))
        
    # Generate audio TTS:
    # 1. Always generate if user sent an audio message
    # 2. Or generate if the user specifically asked for pronunciation ("発音を聞かせて" / "発音" / "발음" in audio_script or text)
    audio_script = feedback_data.get("audio_script", "").strip()
    detected_lang = feedback_data.get("detected_lang", "ko")
    user_text_val = feedback_data.get("user_text", "")
    
    # Determine audio language: if korean_phrase is present, default audio to Korean for listening practice
    audio_lang = "ko" if korean_phrase or detected_lang == "ja" else detected_lang
    
    # Generate audio if it was a voice message OR if the user asked to hear pronunciation
    should_send_audio = is_audio or (
        bool(audio_script) and (
            "発音" in chat_reply or "🔊" in chat_reply or "발음" in chat_reply or
            "発音" in user_text_val or "발음" in user_text_val
        )
    )
    
    if should_send_audio and audio_script:
        try:
            audio_id = str(uuid.uuid4())
            audio_url, audio_duration = generate_tts_audio(audio_script, audio_lang, audio_id)
            messages.append(AudioMessage(original_content_url=audio_url, duration=audio_duration))
        except Exception as ae:
            logger.error(f"Failed to generate TTS audio: {ae}", exc_info=True)
            
    return messages

def send_line_response(reply_token: str, user_id: str, messages: list):
    """Send reply to user using reply token, fall back to push message on timeout."""
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=messages
                )
            )
        logger.info("Reply message sent successfully.")
    except Exception as re:
        logger.warning(f"Reply token failed, falling back to push message: {re}")
        try:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.push_message(
                    PushMessageRequest(
                        to=user_id,
                        messages=messages
                    )
                )
            logger.info("Push message sent successfully.")
        except Exception as pe:
            logger.error(f"Push message fallback also failed: {pe}", exc_info=True)

def send_error_message(reply_token: str, user_id: str, detail: str = ""):
    """Send user-friendly error message using reply or push."""
    error_msg = f"申し訳ありません。エラーが発生しました。\n({detail})" if detail else "申し訳ありません。エラーが発生しました。"
    messages = [TextMessage(text=error_msg)]
    send_line_response(reply_token, user_id, messages)

def process_text_in_background(user_id: str, reply_token: str, user_text: str, source_type: str):
    """Background thread to process text messages."""
    try:
        logger.info(f"[TEXT] Processing message from {user_id}: {user_text[:50]}...")
        
        # Show loading animation for 1:1 chats
        if source_type == "user":
            try:
                show_loading(user_id)
            except Exception as le:
                logger.warning(f"Failed to show loading: {le}")
                
        # Invoke Gemini Client
        logger.info(f"[TEXT] Calling Gemini for user {user_id}...")
        feedback_data = gemini_client.evaluate_korean_text(user_id, user_text)
        feedback_data["user_text"] = user_text
        logger.info(f"[TEXT] Gemini response received for user {user_id}.")
        
        # Format response messages
        messages = format_assistant_response(feedback_data, False)
        logger.info(f"Formatted messages to send: {messages}")
        
        # Send reply
        send_line_response(reply_token, user_id, messages)
        logger.info(f"[TEXT] Response sent to user {user_id}.")
        
    except Exception as e:
        logger.error(f"[TEXT] Exception processing message for {user_id}: {e}\n{traceback.format_exc()}")
        send_error_message(reply_token, user_id, str(e)[:100])

def process_audio_in_background(user_id: str, reply_token: str, message_id: str, source_type: str):
    """Background thread to process audio messages."""
    temp_file_path = ""
    try:
        logger.info(f"[AUDIO] Processing audio from {user_id}, message_id={message_id}...")
        
        if source_type == "user":
            try:
                show_loading(user_id)
            except Exception as le:
                logger.warning(f"Failed to show loading: {le}")
                
        # Download audio from LINE blob API
        temp_file_path = download_audio_content(message_id)
        logger.info(f"[AUDIO] Audio downloaded: {temp_file_path}")
        
        # Evaluate audio
        logger.info(f"[AUDIO] Calling Gemini for user {user_id}...")
        feedback_data = gemini_client.evaluate_korean_audio(user_id, temp_file_path, "audio/mp4")
        logger.info(f"[AUDIO] Gemini response received for user {user_id}.")
        
        # Format response messages (is_audio=True)
        messages = format_assistant_response(feedback_data, True)
        
        # Send reply
        send_line_response(reply_token, user_id, messages)
        logger.info(f"[AUDIO] Response sent to user {user_id}.")
        
    except Exception as e:
        logger.error(f"[AUDIO] Exception processing audio for {user_id}: {e}\n{traceback.format_exc()}")
        send_error_message(reply_token, user_id, str(e)[:100])
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception as oe:
                logger.warning(f"Failed to delete temp file {temp_file_path}: {oe}")

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_id = event.source.user_id
    user_text = event.message.text
    source_type = event.source.type
    reply_token = event.reply_token
    
    logger.info(f"[WEBHOOK] Text message received from {user_id}")
    # Use threading.Thread for reliable background execution from synchronous handler
    thread = threading.Thread(
        target=process_text_in_background,
        args=(user_id, reply_token, user_text, source_type),
        daemon=True
    )
    thread.start()

@handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio_message(event):
    user_id = event.source.user_id
    message_id = event.message.id
    source_type = event.source.type
    reply_token = event.reply_token
    
    logger.info(f"[WEBHOOK] Audio message received from {user_id}")
    thread = threading.Thread(
        target=process_audio_in_background,
        args=(user_id, reply_token, message_id, source_type),
        daemon=True
    )
    thread.start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
