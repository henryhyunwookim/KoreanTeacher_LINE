import json
import os
import sys
import subprocess
import tempfile
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from app.config import get_gcs_bucket_name, get_gcp_project

logger = logging.getLogger(__name__)

# Temporary directory cache paths to prevent workspace pollution
DEFAULT_STATE_BLOB = "korean_teacher/profile_memory.json"
DEFAULT_LOG_BLOB = "korean_teacher/run_log.json"

LOCAL_CACHE_DIR = tempfile.gettempdir()


def _get_local_cache_path(blob_name: str) -> str:
    safe_name = blob_name.replace("/", "_").replace("\\", "_")
    return os.path.join(LOCAL_CACHE_DIR, safe_name)


def load_cloud_state(blob_name: str = DEFAULT_STATE_BLOB, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Loads state from GCS with SDK, gcloud CLI fallback, and OS temp cache fallback."""
    bucket_name = get_gcs_bucket_name()
    local_cache_path = _get_local_cache_path(blob_name)
    fallback_value = default if default is not None else {}

    # Method 1: Google Cloud Storage SDK
    try:
        from google.cloud import storage

        client = storage.Client(project=get_gcp_project())
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        if blob.exists():
            content = blob.download_as_text(encoding="utf-8")
            data = json.loads(content)
            # Sync to local temp cache
            try:
                with open(local_cache_path, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception:
                pass
            return data
    except Exception as sdk_err:
        logger.debug(f"GCS SDK load failed for {blob_name}: {sdk_err}")

    # Method 2: gcloud CLI fallback
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
        logger.debug(f"gcloud storage cat failed for {blob_name}: {cli_err}")

    # Method 3: Local OS temporary directory cache fallback
    if os.path.exists(local_cache_path):
        try:
            with open(local_cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as cache_err:
            logger.debug(f"Temp cache read failed for {local_cache_path}: {cache_err}")

    return fallback_value


def save_cloud_state(data: Dict[str, Any], blob_name: str = DEFAULT_STATE_BLOB) -> bool:
    """Saves state directly to GCS and OS temporary cache."""
    bucket_name = get_gcs_bucket_name()
    local_cache_path = _get_local_cache_path(blob_name)
    data_str = json.dumps(data, ensure_ascii=False, indent=2)

    # Save to OS temp cache first
    try:
        with open(local_cache_path, "w", encoding="utf-8") as f:
            f.write(data_str)
    except Exception as cache_err:
        logger.warning(f"Failed to write state to temp cache {local_cache_path}: {cache_err}")

    # Upload to GCS via SDK
    try:
        from google.cloud import storage

        client = storage.Client(project=get_gcp_project())
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(data_str, content_type="application/json")
        return True
    except Exception as sdk_err:
        logger.debug(f"GCS SDK upload failed for {blob_name}: {sdk_err}")

    # Fallback to gcloud storage CLI upload
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
        logger.warning(f"gcloud storage cp fallback failed for {blob_name}: {cli_err}")
        return False


def append_run_log(log_entry: Dict[str, Any], blob_name: str = DEFAULT_LOG_BLOB) -> bool:
    """Appends an operational or audit execution log entry decoupled from user state.

    Also streams structured JSON to stdout for automatic Cloud Logging ingestion on Cloud Run.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    enriched_entry = {
        "timestamp": timestamp,
        **log_entry
    }

    # Stream structured log to stdout (Cloud Run / Cloud Logging best practice)
    print(f"[AUDIT_LOG] {json.dumps(enriched_entry, ensure_ascii=False)}", flush=True)

    # Persist to GCS execution log buffer
    try:
        existing_logs: List[Dict[str, Any]] = load_cloud_state(blob_name, default=[])  # type: ignore
        if not isinstance(existing_logs, list):
            existing_logs = []

        existing_logs.append(enriched_entry)
        # Keep recent 500 operational log events to manage payload size
        if len(existing_logs) > 500:
            existing_logs = existing_logs[-500:]

        return save_cloud_state(existing_logs, blob_name=blob_name)  # type: ignore
    except Exception as e:
        logger.warning(f"Failed to append to operational GCS log {blob_name}: {e}")
        return False
