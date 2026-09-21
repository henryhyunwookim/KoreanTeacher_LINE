"""CLI Tool to Inspect Inactive Users and Trigger Proactive Check-in Messages.

=============================================================================
PURPOSE:
    Provides developers and administrators with a CLI runner to:
      1. Inspect inactive users who haven't messaged the bot in 3+ days.
      2. Preview personalized Kim Hyunwoo check-in messages (--dry-run).
      3. Discretely trigger proactive LINE push messages (--send).
      4. Test single-user check-in generation (--user-id).

USAGE:
    # Dry run (preview eligible users & generated messages without sending):
    py scripts/trigger_check_in.py --dry-run

    # Send check-in messages to eligible users:
    py scripts/trigger_check_in.py --send

    # Test preview for a specific user ID:
    py scripts/trigger_check_in.py --user-id U12345678 --dry-run
=============================================================================
"""

import sys
import os
import argparse
import json
import logging

# Ensure project root is in sys.path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app import gemini_client
from app.config import get_line_channel_access_token, get_setting
from app.memory import append_run_log

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="KoreanTeacher_LINE Inactivity Check-in Runner")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Preview without sending LINE push messages (default: True)")
    parser.add_argument("--send", action="store_true", help="Actually dispatch LINE push messages to eligible users")
    parser.add_argument("--user-id", type=str, default="", help="Target a specific LINE user ID")
    parser.add_argument("--min-days", type=int, default=3, help="Minimum days of inactivity (default: 3)")
    parser.add_argument("--max-days", type=int, default=14, help="Maximum days of inactivity (default: 14)")
    parser.add_argument("--cooldown-days", type=int, default=7, help="Minimum days between check-ins (default: 7)")
    args = parser.parse_args()

    # If --send is explicitly passed, disable dry_run
    is_dry_run = not args.send if args.send else args.dry_run

    print("=" * 70)
    print(" KoreanTeacher_LINE Proactive Inactivity Check-in Runner")
    print(f" Mode: {'DRY RUN (Preview Only)' if is_dry_run else 'LIVE (Sending LINE Push Messages)'}")
    print(f" Criteria: Inactive between {args.min_days} and {args.max_days} days (cooldown: {args.cooldown_days} days)")
    print("=" * 70)

    if args.user_id:
        eligible_users = [{
            "user_id": args.user_id,
            "inactive_days": "manual",
            "proficiency_level": "auto",
            "last_active_at": "manual",
            "last_checkin_at": "manual"
        }]
    else:
        print("\nScanning user base for inactive students...")
        eligible_users = gemini_client.find_inactive_users(
            min_days=args.min_days,
            max_days=args.max_days,
            cooldown_days=args.cooldown_days
        )

    print(f"Found {len(eligible_users)} eligible user(s).\n")

    if not eligible_users:
        print("No users currently meet the check-in criteria.")
        return

    for i, user_info in enumerate(eligible_users, 1):
        uid = user_info["user_id"]
        inactive_days = user_info.get("inactive_days")
        print(f"[{i}/{len(eligible_users)}] User: {uid} (Inactive: ~{inactive_days} days)")
        
        try:
            print("  Generating personalized check-in from Kim Hyunwoo...")
            checkin_data = gemini_client.generate_checkin_message(uid)
            chat_reply = checkin_data.get("chat_reply", "").strip()
            quick_replies = checkin_data.get("quick_replies", [])
            
            print(f"  Message:\n    {chat_reply.replace(chr(10), chr(10) + '    ')}")
            if quick_replies:
                print(f"  Quick Replies: {quick_replies}")

            if not is_dry_run:
                # LINE Push sending
                from linebot.v3.messaging import (
                    Configuration,
                    ApiClient,
                    MessagingApi,
                    PushMessageRequest,
                    TextMessage,
                    QuickReply,
                    QuickReplyItem,
                    MessageAction
                )
                from app.main import get_line_configuration
                
                qr_items = []
                for qr in quick_replies:
                    label = str(qr).strip()[:20]
                    if label:
                        qr_items.append(QuickReplyItem(action=MessageAction(label=label, text=label)))
                qr_obj = QuickReply(items=qr_items) if qr_items else None
                
                with ApiClient(get_line_configuration()) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.push_message(
                        PushMessageRequest(
                            to=uid,
                            messages=[TextMessage(text=chat_reply, quick_reply=qr_obj)]
                        )
                    )
                gemini_client.record_user_checkin(uid)
                append_run_log({
                    "event": "proactive_checkin_cli",
                    "user_id": uid,
                    "inactive_days": inactive_days,
                    "status": "sent"
                })
                print("  [DISPATCHED] LINE push message delivered successfully.")
            else:
                print("  [DRY RUN] Message generated successfully (not sent).")
        except Exception as e:
            print(f"  [ERROR] Failed to process user {uid}: {e}")

    print("\nCompleted.")


if __name__ == "__main__":
    main()
