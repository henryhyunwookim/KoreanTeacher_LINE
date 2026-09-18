"""Multi-PC Google Cloud Secret & Storage Sync Utility.

Allows pushing local environment secrets to Secret Manager, initializing Cloud Storage,
and testing dry-run resolution on any machine authenticated via gcloud.
"""

import argparse
import os
import sys
import subprocess
import json
from typing import Dict, Optional


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    is_win = sys.platform == "win32"
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
        shell=is_win
    )


def get_current_project(override: Optional[str] = None) -> str:
    if override:
        return override
    proj = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    if proj:
        return proj
    try:
        res = run_cmd(["gcloud", "config", "get-value", "project"], check=False)
        val = res.stdout.strip()
        if val and val != "(unset)":
            return val
    except Exception:
        pass
    raise RuntimeError("Could not determine GCP project. Run 'gcloud config set project <id>' or pass --project.")


def ensure_secret(secret_id: str, value: str, project_id: str):
    """Creates a secret if missing, or adds a new version if changed."""
    print(f"[*] Ensuring secret '{secret_id}' in project '{project_id}'...")

    # Check if secret exists
    check_cmd = ["gcloud", "secrets", "describe", secret_id, f"--project={project_id}"]
    res = run_cmd(check_cmd, check=False)
    if res.returncode != 0:
        print(f"  -> Secret '{secret_id}' not found. Creating...")
        create_cmd = [
            "gcloud", "secrets", "create", secret_id,
            f"--project={project_id}",
            "--replication-policy=automatic"
        ]
        run_cmd(create_cmd)
        print(f"  -> Secret '{secret_id}' created successfully.")

    # Add secret version by piping value via stdin
    is_win = sys.platform == "win32"
    add_cmd = [
        "gcloud", "secrets", "versions", "add", secret_id,
        f"--project={project_id}",
        "--data-file=-"
    ]
    p = subprocess.run(
        add_cmd,
        input=value,
        text=True,
        capture_output=True,
        check=True,
        shell=is_win
    )
    print(f"  -> Successfully updated secret version for '{secret_id}'.")


def push_env_file(env_path: str, project_id: str):
    """Parses a local .env file and pushes secrets to Secret Manager."""
    if not os.path.exists(env_path):
        print(f"[!] Error: File '{env_path}' not found.")
        sys.exit(1)

    # Key mapping to Secret Manager IDs
    SECRET_KEY_MAPPINGS = {
        "GEMINI_API_KEY": "gemini-api-key",
        "LINE_CHANNEL_SECRET": "korean-teacher-line-channel-secret",
        "LINE_CHANNEL_ACCESS_TOKEN": "korean-teacher-line-channel-access-token",
        "NAVER_CLIENT_ID": "naver-client-id",
        "NAVER_CLIENT_SECRET": "naver-client-secret",
        "KAKAO_REST_API_KEY": "kakao-rest-api-key",
        "GOOGLE_SEARCH_API_KEY": "google-search-api-key",
        "GOOGLE_SEARCH_CX": "google-search-cx",
    }

    print(f"[*] Reading secrets from {env_path}...")
    secrets_to_push: Dict[str, str] = {}
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("\"'")
            if key in SECRET_KEY_MAPPINGS and val:
                secrets_to_push[SECRET_KEY_MAPPINGS[key]] = val

    if not secrets_to_push:
        print("[!] No recognized secret keys found in env file.")
        return

    for secret_id, value in secrets_to_push.items():
        try:
            ensure_secret(secret_id, value, project_id)
        except Exception as e:
            print(f"[!] Failed to push secret {secret_id}: {e}")

    print("[+] All secrets synchronized to Secret Manager!")


def init_gcs_bucket(project_id: str, location: str = "asia-northeast1"):
    """Ensures the Cloud Storage state & audit bucket exists."""
    bucket_name = f"{project_id}-korean-teacher-data"
    print(f"[*] Checking Cloud Storage bucket gs://{bucket_name}...")

    check_cmd = ["gcloud", "storage", "buckets", "describe", f"gs://{bucket_name}"]
    res = run_cmd(check_cmd, check=False)
    if res.returncode == 0:
        print(f"[+] Bucket gs://{bucket_name} already exists.")
        return

    print(f"[*] Creating bucket gs://{bucket_name} in {location}...")
    create_cmd = [
        "gcloud", "storage", "buckets", "create", f"gs://{bucket_name}",
        f"--project={project_id}",
        f"--location={location}",
        "--uniform-bucket-level-access"
    ]
    run_cmd(create_cmd)
    print(f"[+] Bucket gs://{bucket_name} created successfully.")


def run_dry_run(project_id: str):
    """Verifies that secrets and storage can be resolved without local .env files."""
    print("=" * 60)
    print("Multi-PC Zero-Setup Verification (Dry Run)")
    print("=" * 60)
    print(f"Target GCP Project: {project_id}")

    # Check gcloud authentication
    auth_res = run_cmd(["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"], check=False)
    active_account = auth_res.stdout.strip()
    print(f"Active gcloud Account: {active_account or '[!] Not authenticated'}")

    # Test Secret Resolution
    print("\n[*] Testing Secret Manager access...")
    try:
        from app.config import resolve_cloud_secret

        # Test Gemini API key resolution
        gemini_val = resolve_cloud_secret("gemini-api-key", project_id=project_id)
        if gemini_val:
            masked = f"{gemini_val[:4]}...{gemini_val[-4:]}" if len(gemini_val) > 8 else "***"
            print(f"  [+] 'gemini-api-key' resolved successfully: {masked} (length: {len(gemini_val)})")
        else:
            print("  [-] 'gemini-api-key' could not be resolved from Secret Manager.")

        # Test LINE Channel Secret
        line_secret = resolve_cloud_secret("korean-teacher-line-channel-secret", project_id=project_id)
        if line_secret:
            masked = f"{line_secret[:4]}...{line_secret[-4:]}" if len(line_secret) > 8 else "***"
            print(f"  [+] 'korean-teacher-line-channel-secret' resolved: {masked}")
        else:
            print("  [-] 'korean-teacher-line-channel-secret' not yet registered in Secret Manager.")

        # Test LINE Channel Access Token
        line_token = resolve_cloud_secret("korean-teacher-line-channel-access-token", project_id=project_id)
        if line_token:
            masked = f"{line_token[:4]}...{line_token[-4:]}" if len(line_token) > 8 else "***"
            print(f"  [+] 'korean-teacher-line-channel-access-token' resolved: {masked}")
        else:
            print("  [-] 'korean-teacher-line-channel-access-token' not yet registered in Secret Manager.")

    except Exception as e:
        print(f"  [!] Error resolving secrets: {e}")

    # Test Cloud Storage access
    print("\n[*] Testing Cloud Storage state persistence...")
    bucket_name = f"{project_id}-korean-teacher-data"
    bucket_check = run_cmd(["gcloud", "storage", "buckets", "describe", f"gs://{bucket_name}"], check=False)
    if bucket_check.returncode == 0:
        print(f"  [+] Bucket gs://{bucket_name} is accessible.")
    else:
        print(f"  [-] Bucket gs://{bucket_name} not found. Run 'python sync_secrets.py --init-bucket' to create it.")

    print("\n[+] Verification finished.")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Multi-PC Cloud Migration Secret & Storage Sync Utility")
    parser.add_argument("--project", help="Google Cloud Project ID")
    parser.add_argument("--push-env", help="Path to local .env file to push to Secret Manager")
    parser.add_argument("--init-bucket", action="store_true", help="Ensure Cloud Storage bucket is created")
    parser.add_argument("--dry-run", action="store_true", help="Test secret resolution and connectivity")

    args = parser.parse_args()

    # Default to dry run if no specific action provided
    if not args.push_env and not args.init_bucket and not args.dry_run:
        args.dry_run = True

    try:
        project_id = get_current_project(args.project)
    except Exception as e:
        print(f"[!] Error: {e}")
        sys.exit(1)

    if args.init_bucket:
        init_gcs_bucket(project_id)

    if args.push_env:
        push_env_file(args.push_env, project_id)

    if args.dry_run:
        run_dry_run(project_id)


if __name__ == "__main__":
    main()
