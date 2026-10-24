"""
Neon Object Storage integration (S3-compatible).

Neon speaks the S3 API with path-style addressing and SigV4, so we configure
boto3 with the injected AWS_* env vars and force path style. The bucket name
(`assets`) matches the bucket declared in neon.ts.

boto3 is synchronous; async callers wrap calls in `asyncio.to_thread`.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import get_settings

logger = logging.getLogger(__name__)

_client = None


def get_client():
    """Lazy singleton S3 client wired to the Neon branch's storage endpoint."""
    global _client
    if _client is not None:
        return _client
    s = get_settings()
    _client = boto3.client(
        "s3",
        endpoint_url=s.aws_endpoint_url_s3,
        aws_access_key_id=s.aws_access_key_id,
        aws_secret_access_key=s.aws_secret_access_key,
        region_name=s.aws_region or "us-east-2",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return _client


def bucket_name() -> str:
    return get_settings().storage_bucket_name


def is_configured() -> bool:
    s = get_settings()
    return all([s.aws_access_key_id, s.aws_secret_access_key, s.aws_endpoint_url_s3])


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def head_bucket() -> bool:
    """Return True if the configured bucket is reachable."""
    try:
        get_client().head_bucket(Bucket=bucket_name())
        return True
    except ClientError as exc:
        logger.warning("Neon Object Storage head_bucket(%s) failed: %s", bucket_name(), exc)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neon Object Storage check failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Object read/write
# ---------------------------------------------------------------------------

def put_json(key: str, obj: Any) -> str:
    """Upload a Python object as JSON and return the object key."""
    body = json.dumps(obj, default=str).encode("utf-8")
    get_client().put_object(
        Bucket=bucket_name(),
        Key=key,
        Body=body,
        ContentType="application/json",
    )
    logger.info("Uploaded s3://%s/%s (%d bytes)", bucket_name(), key, len(body))
    return key


def get_json(key: str) -> Any:
    """Download a JSON object and return the parsed Python object."""
    resp = get_client().get_object(Bucket=bucket_name(), Key=key)
    body = resp["Body"].read()
    return json.loads(body)


def object_exists(key: str) -> bool:
    try:
        get_client().head_object(Bucket=bucket_name(), Key=key)
        return True
    except ClientError:
        return False


def list_keys(prefix: str = "", limit: int = 100) -> list[str]:
    paginator = get_client().get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=bucket_name(), Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            keys.append(obj["Key"])
            if len(keys) >= limit:
                return keys
    return keys
