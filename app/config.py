import os
import sys
import subprocess
import logging
from typing import Optional, List
from dotenv import load_dotenv

# Load local .env if available (for local dev override)
load_dotenv()

logger = logging.getLogger(__name__)

_SECRETS_CACHE: dict[str, str] = {}
_GCP_PROJECT: Optional[str] = None


def get_gcp_project() -> Optional[str]:
    """Retrieves the current GCP Project ID from environment or gcloud config."""
    global _GCP_PROJECT
    if _GCP_PROJECT:
        return _GCP_PROJECT

    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    if project:
        _GCP_PROJECT = project
        return _GCP_PROJECT

    try:
        is_win = sys.platform == "win32"
        cmd = ["gcloud", "config", "get-value", "project"]
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
            shell=is_win
        )
        val = res.stdout.strip()
        if val and val != "(unset)":
            _GCP_PROJECT = val
            return _GCP_PROJECT
    except Exception as e:
        logger.debug(f"Failed to get project from gcloud: {e}")

    return None


def resolve_cloud_secret(secret_id: str, project_id: Optional[str] = None) -> Optional[str]:
    """Resolves a secret from GCP Secret Manager via SDK, falling back to gcloud CLI.

    Supports multi-PC portability without requiring manual credential downloads.
    """
    if not secret_id:
        return None

    if secret_id in _SECRETS_CACHE:
        return _SECRETS_CACHE[secret_id]

    proj = project_id or get_gcp_project()
    if not proj:
        return None

    # Method 1: Google Cloud Secret Manager SDK
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{proj}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        secret_value = response.payload.data.decode("utf-8").strip()
        if secret_value:
            _SECRETS_CACHE[secret_id] = secret_value
            return secret_value
    except Exception as sdk_err:
        logger.debug(f"Secret Manager SDK resolution failed for {secret_id}: {sdk_err}")

    # Method 2: gcloud CLI fallback (works on any PC authenticated via 'gcloud auth login')
    try:
        is_win = sys.platform == "win32"
        cmd = [
            "gcloud",
            "secrets",
            "versions",
            "access",
            "latest",
            f"--secret={secret_id}",
            f"--project={proj}",
        ]
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
            shell=is_win
        )
        secret_value = res.stdout.strip()
        if secret_value:
            _SECRETS_CACHE[secret_id] = secret_value
            return secret_value
    except Exception as cli_err:
        logger.debug(f"gcloud CLI secret access failed for {secret_id}: {cli_err}")

    return None


def get_setting(
    env_key: str,
    secret_names: Optional[List[str]] = None,
    default: Optional[str] = None
) -> Optional[str]:
    """Gets a setting from environment variable, Secret Manager, or default.

    If not found in environment, checks Secret Manager using candidate secret names.
    Automatically populates os.environ once resolved so external SDKs pick it up.
    """
    val = os.environ.get(env_key)
    if val:
        return val

    # Candidate secret names in Secret Manager
    candidates = secret_names or [
        env_key.lower().replace("_", "-"),
        f"korean-teacher-{env_key.lower().replace('_', '-')}"
    ]

    for candidate in candidates:
        secret_val = resolve_cloud_secret(candidate)
        if secret_val:
            os.environ[env_key] = secret_val
            return secret_val

    if default is not None:
        return default

    return None


# Helper properties for common configuration values
def get_gemini_api_key() -> Optional[str]:
    return get_setting("GEMINI_API_KEY", ["gemini-api-key", "korean-teacher-gemini-api-key"])


def get_line_channel_secret() -> Optional[str]:
    return get_setting("LINE_CHANNEL_SECRET", ["korean-teacher-line-channel-secret", "line-channel-secret"])


def get_line_channel_access_token() -> Optional[str]:
    return get_setting("LINE_CHANNEL_ACCESS_TOKEN", ["korean-teacher-line-channel-access-token", "line-channel-access-token"])


def get_gcs_bucket_name() -> str:
    proj = get_gcp_project() or "korean-teacher"
    return get_setting("GCS_BUCKET_NAME", ["korean-teacher-bucket-name"], default=f"{proj}-korean-teacher-data")
