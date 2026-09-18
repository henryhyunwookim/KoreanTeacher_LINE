"""Configuration and Secret Resolution Module.

=============================================================================
PURPOSE:
    Provides robust, dual-mode configuration management for KoreanTeacher_LINE.
    Supports seamless multi-PC developer portability and zero-credential Cloud Run
    deployments by resolving environment settings with a multi-layered fallback strategy:
      1. Local environment variables (or local .env if present).
      2. Google Cloud Secret Manager SDK (via Application Default Credentials).
      3. Google Cloud SDK CLI fallback (`gcloud secrets versions access latest`).
      4. Safe default fallback values.

PREREQUISITES:
    - On local machines: `gcloud auth login` or ADC credentials.
    - On Cloud Run: Application Default Credentials from the default Compute SA.
=============================================================================
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Dict, List, Optional
from dotenv import load_dotenv

# Load local .env if available (for local development override)
load_dotenv()

logger = logging.getLogger(__name__)

# =============================================================================
# Global Caches & State
# =============================================================================

# In-memory memoization cache to prevent repeated Secret Manager API / CLI calls
_SECRETS_CACHE: Dict[str, str] = {}
_GCP_PROJECT: Optional[str] = None


# =============================================================================
# GCP Project ID Resolution
# =============================================================================

def get_gcp_project() -> Optional[str]:
    """Retrieves the active Google Cloud Project ID.

    Resolution order:
      1. Cached in-memory project ID.
      2. GOOGLE_CLOUD_PROJECT or GCP_PROJECT environment variables.
      3. Active gcloud CLI configuration (`gcloud config get-value project`).

    Returns:
        The resolved GCP project ID string, or None if undetermined.
    """
    global _GCP_PROJECT
    if _GCP_PROJECT:
        return _GCP_PROJECT

    # Step 1: Check standard Google Cloud environment variables
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    if project:
        _GCP_PROJECT = project
        return _GCP_PROJECT

    # Step 2: Fallback to gcloud CLI query for local multi-PC development
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
        logger.debug(f"Failed to query active project from gcloud CLI: {e}")

    return None


# =============================================================================
# Cloud Secret Resolution (SDK + CLI Fallback)
# =============================================================================

def resolve_cloud_secret(secret_id: str, project_id: Optional[str] = None) -> Optional[str]:
    """Resolves a secret from Google Cloud Secret Manager with dual-layer fallback.

    Method 1: Google Cloud Secret Manager Python SDK (fastest, standard on Cloud Run ADC).
    Method 2: gcloud CLI fallback (works immediately on any developer PC with `gcloud auth login`).

    Args:
        secret_id: The ID/name of the secret in Secret Manager (e.g. 'gemini-api-key').
        project_id: Optional explicit GCP project ID.

    Returns:
        The decrypted secret string, or None if unavailable.
    """
    if not secret_id:
        return None

    # Return cached value if already retrieved during runtime
    if secret_id in _SECRETS_CACHE:
        return _SECRETS_CACHE[secret_id]

    proj = project_id or get_gcp_project()
    if not proj:
        return None

    # Method 1: Google Cloud Secret Manager Python SDK
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
        logger.debug(f"Secret Manager SDK resolution failed for '{secret_id}': {sdk_err}")

    # Method 2: gcloud CLI fallback
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
        logger.debug(f"gcloud CLI secret access fallback failed for '{secret_id}': {cli_err}")

    return None


# =============================================================================
# Generalized Setting Getter
# =============================================================================

def get_setting(
    env_key: str,
    secret_names: Optional[List[str]] = None,
    default: Optional[str] = None
) -> Optional[str]:
    """Retrieves a configuration setting with multi-tier resolution.

    Checks:
      1. os.environ for the given `env_key`.
      2. Cloud Secret Manager using candidate names.
      3. Default fallback value.

    When resolved from Secret Manager, automatically populates `os.environ`
    so downstream external libraries (such as google-genai) can discover it.

    Args:
        env_key: The target environment variable name (e.g. 'GEMINI_API_KEY').
        secret_names: List of candidate Secret Manager secret IDs to probe.
        default: Fallback default value if not resolved elsewhere.

    Returns:
        The resolved configuration string, or default.
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


# =============================================================================
# Strongly-Typed Setting Helpers
# =============================================================================

def get_gemini_api_key() -> Optional[str]:
    """Returns the Google Gemini API key."""
    return get_setting("GEMINI_API_KEY", ["gemini-api-key", "korean-teacher-gemini-api-key"])


def get_line_channel_secret() -> Optional[str]:
    """Returns the LINE Messaging API Channel Secret."""
    return get_setting("LINE_CHANNEL_SECRET", ["korean-teacher-line-channel-secret", "line-channel-secret"])


def get_line_channel_access_token() -> Optional[str]:
    """Returns the LINE Messaging API Channel Access Token."""
    return get_setting("LINE_CHANNEL_ACCESS_TOKEN", ["korean-teacher-line-channel-access-token", "line-channel-access-token"])


def get_gcs_bucket_name() -> str:
    """Returns the Google Cloud Storage bucket name for state & audit storage."""
    proj = get_gcp_project() or "korean-teacher"
    return get_setting("GCS_BUCKET_NAME", ["korean-teacher-bucket-name"], default=f"{proj}-korean-teacher-data") or f"{proj}-korean-teacher-data"
