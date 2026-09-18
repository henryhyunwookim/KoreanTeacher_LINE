"""Cloud Storage State Persistence and Decoupled Audit Logging.

=============================================================================
PURPOSE:
    Provides resilient, dual-layer cloud persistence for KoreanTeacher_LINE:
      1. Cloud Storage (GCS) State: Persists user profiles and conversation cache
         across stateless Cloud Run container lifecycles.
      2. OS Temporary Directory Cache: Serves as an immediate local read/write buffer
         to eliminate workspace pollution while maintaining offline/transient durability.
      3. Structured Audit Logging: Streams JSON audit logs directly to stdout for
         Cloud Logging ingestion on Google Cloud Run while maintaining a rolling
         GCS execution buffer.

DESIGN RATIONALE:
    Cloud Run containers are ephemeral and stateless. User profiles and audit
    trails must survive container scale-to-zero and redeployments. Using GCS
    provides durable, low-cost persistence without requiring dedicated databases
    for lightweight state blobs.
=============================================================================
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import get_gcp_project, get_gcs_bucket_name

logger = logging.getLogger(__name__)

# =============================================================================
# Constants & Storage Blob Paths
# =============================================================================

DEFAULT_STATE_BLOB: str = "korean_teacher/profile_memory.json"
DEFAULT_LOG_BLOB: str = "korean_teacher/run_log.json"

# Local OS temp directory cache prevents pollution of repository files
LOCAL_CACHE_DIR: str = tempfile.gettempdir()


def _get_local_cache_path(blob_name: str) -> str:
    """Generates a sanitized local OS temporary cache file path for a GCS blob.

    Args:
        blob_name: Target GCS blob name (e.g. 'korean_teacher/profile_memory.json').

    Returns:
        Absolute path to local temporary cache file.
    """
    safe_name = blob_name.replace("/", "_").replace("\\", "_")
    return os.path.join(LOCAL_CACHE_DIR, safe_name)


# =============================================================================
# Cloud Storage State Persistence (Load & Save)
# =============================================================================

def load_cloud_state(
    blob_name: str = DEFAULT_STATE_BLOB,
    default: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Loads state JSON from Google Cloud Storage with multi-tier fallback.

    Resolution Pipeline:
      1. GCS Python SDK download via Cloud Run ADC or active credentials.
      2. gcloud CLI fallback (`gcloud storage cat gs://...`) for local CLI users.
      3. Local OS temp directory cache fallback if offline or storage is unreachable.
      4. Supplied default empty dict.

    Args:
        blob_name: The GCS object key name.
        default: Fallback dictionary if state is missing or inaccessible.

    Returns:
        Loaded state as a Python dictionary.
    """
    bucket_name = get_gcs_bucket_name()
    local_cache_path = _get_local_cache_path(blob_name)
    fallback_value: Dict[str, Any] = default if default is not None else {}

    # Step 1: Attempt download via Google Cloud Storage SDK
    try:
        from google.cloud import storage

        client = storage.Client(project=get_gcp_project())
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        if blob.exists():
            content = blob.download_as_text(encoding="utf-8")
            data: Dict[str, Any] = json.loads(content)

            # Sync downloaded content to local OS temp cache
            try:
                with open(local_cache_path, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception:
                pass
            return data
    except Exception as sdk_err:
        logger.debug(f"GCS SDK download failed for '{blob_name}': {sdk_err}")

    # Step 2: Fallback to gcloud CLI storage command
    try:
        is_win = sys.platform == "win32"
        cmd = ["gcloud", "storage", "cat", f"gs://{bucket_name}/{blob_name}"]
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=12,
            shell=is_win
        )
        if res.stdout:
            data = json.loads(res.stdout)
            try:
                with open(local_cache_path, "w", encoding="utf-8") as f:
                    f.write(res.stdout)
            except Exception:
                pass
            return data
    except Exception as cli_err:
        logger.debug(f"gcloud storage cat fallback failed for '{blob_name}': {cli_err}")

    # Step 3: Fallback to local OS temporary cache file
    if os.path.exists(local_cache_path):
        try:
            with open(local_cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as cache_err:
            logger.debug(f"Temp cache read failed for '{local_cache_path}': {cache_err}")

    return fallback_value


def save_cloud_state(data: Dict[str, Any], blob_name: str = DEFAULT_STATE_BLOB) -> bool:
    """Persists state data directly to GCS and the local OS temp cache.

    Args:
        data: Dictionary payload to serialize and store.
        blob_name: Target GCS blob name.

    Returns:
        True if upload or local persistence succeeded, False otherwise.
    """
    bucket_name = get_gcs_bucket_name()
    local_cache_path = _get_local_cache_path(blob_name)
    data_str = json.dumps(data, ensure_ascii=False, indent=2)

    # Step 1: Always update local OS temp cache first for speed and local recovery
    try:
        with open(local_cache_path, "w", encoding="utf-8") as f:
            f.write(data_str)
    except Exception as cache_err:
        logger.warning(f"Failed to write state to temp cache '{local_cache_path}': {cache_err}")

    # Step 2: Upload to GCS via Python SDK
    try:
        from google.cloud import storage

        client = storage.Client(project=get_gcp_project())
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(data_str, content_type="application/json")
        return True
    except Exception as sdk_err:
        logger.debug(f"GCS SDK upload failed for '{blob_name}': {sdk_err}")

    # Step 3: Fallback to gcloud storage CLI upload
    try:
        is_win = sys.platform == "win32"
        cmd = ["gcloud", "storage", "cp", local_cache_path, f"gs://{bucket_name}/{blob_name}"]
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
            shell=is_win
        )
        return True
    except Exception as cli_err:
        logger.warning(f"gcloud storage cp fallback failed for '{blob_name}': {cli_err}")
        return False


# =============================================================================
# Decoupled Audit Logging Pipeline
# =============================================================================

def append_run_log(log_entry: Dict[str, Any], blob_name: str = DEFAULT_LOG_BLOB) -> bool:
    """Appends an operational or audit execution log entry decoupled from user state.

    Streams structured JSON with an '[AUDIT_LOG]' marker to stdout so Google Cloud Run
    automatically ingests it into Cloud Logging for real-time monitoring and alerting.
    Also retains a rolling buffer of recent events in Cloud Storage.

    Args:
        log_entry: Dictionary containing audit payload (event type, latency, status, etc.).
        blob_name: Target GCS blob for rolling operational log history.

    Returns:
        True if persisted successfully to GCS, False otherwise.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    enriched_entry: Dict[str, Any] = {
        "timestamp": timestamp,
        **log_entry
    }

    # Step 1: Stream structured log to stdout for Google Cloud Logging ingestion
    print(f"[AUDIT_LOG] {json.dumps(enriched_entry, ensure_ascii=False)}", flush=True)

    # Step 2: Persist to GCS rolling log buffer
    try:
        existing_logs: List[Dict[str, Any]] = load_cloud_state(blob_name, default=[])  # type: ignore
        if not isinstance(existing_logs, list):
            existing_logs = []

        existing_logs.append(enriched_entry)

        # Keep the most recent 500 operational log events to manage payload size
        if len(existing_logs) > 500:
            existing_logs = existing_logs[-500:]

        return save_cloud_state(existing_logs, blob_name=blob_name)  # type: ignore
    except Exception as e:
        logger.warning(f"Failed to append to operational GCS log '{blob_name}': {e}")
        return False
