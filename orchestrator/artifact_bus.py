"""Versioned document store shared by all agents (the "artifact bus").

Agents never pass chat history to each other; they read and write documents here.
Local disk is the working copy. When an S3 bucket is configured every write is mirrored
to S3, and ``pull()`` restores the working copy (used by the AgentCore runtime, whose
local disk does not survive between sessions).
"""

from __future__ import annotations

import difflib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _validate_name(name: str) -> str:
    norm = os.path.normpath(name).replace("\\", "/")
    if not name or os.path.isabs(name) or norm.startswith("..") or "/../" in f"/{norm}/" or norm.startswith("/"):
        raise ValueError(f"Unsafe artifact name: {name!r}")
    return norm


class S3Mirror:
    def __init__(self, bucket: str, prefix: str, client: Any = None):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        if client is None:
            import boto3

            client = boto3.client("s3")
        self.client = client

    def key(self, name: str) -> str:
        return f"{self.prefix}/{name}"

    def put(self, name: str, body: bytes) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=self.key(name),
            Body=body,
            ServerSideEncryption="AES256",
        )

    def pull_all(self, dest_dir: str) -> int:
        count = 0
        token: Optional[str] = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": self.prefix + "/"}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.client.list_objects_v2(**kwargs)
            for obj in page.get("Contents", []):
                rel = _validate_name(obj["Key"][len(self.prefix) + 1 :])
                path = os.path.join(dest_dir, rel)
                os.makedirs(os.path.dirname(path) or dest_dir, exist_ok=True)
                body = self.client.get_object(Bucket=self.bucket, Key=obj["Key"])["Body"].read()
                with open(path, "wb") as fh:
                    fh.write(body)
                count += 1
            if not page.get("IsTruncated"):
                return count
            token = page.get("NextContinuationToken")


class ArtifactBus:
    METADATA = "artifacts_metadata.json"
    HISTORY = ".history"

    def __init__(self, workspace_dir: str, stage: str = "beta", mirror: Optional[S3Mirror] = None):
        self.workspace_dir = workspace_dir
        self.stage = stage
        self.mirror = mirror
        os.makedirs(self.workspace_dir, exist_ok=True)
        self.metadata: Dict[str, Dict[str, Any]] = self._load_metadata()

    # ------------------------------------------------------------------
    def _meta_path(self) -> str:
        return os.path.join(self.workspace_dir, self.METADATA)

    def _load_metadata(self) -> Dict[str, Dict[str, Any]]:
        try:
            with open(self._meta_path(), "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_metadata(self) -> None:
        body = json.dumps(self.metadata, indent=2, sort_keys=True)
        with open(self._meta_path(), "w", encoding="utf-8") as fh:
            fh.write(body)
        if self.mirror:
            self.mirror.put(self.METADATA, body.encode("utf-8"))

    def _write_file(self, rel: str, content: str) -> str:
        path = os.path.join(self.workspace_dir, rel)
        os.makedirs(os.path.dirname(path) or self.workspace_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        if self.mirror:
            self.mirror.put(rel, content.encode("utf-8"))
        return path

    # ------------------------------------------------------------------
    def pull(self) -> int:
        """Restore the working copy from S3 (no-op without a mirror)."""
        if not self.mirror:
            return 0
        count = self.mirror.pull_all(self.workspace_dir)
        self.metadata = self._load_metadata()
        return count

    def write_artifact(self, name: str, content: str, author: str, note: str = "") -> str:
        rel = _validate_name(name)
        previous = self.read_artifact(rel) if self.exists(rel) else None
        prev_meta = self.metadata.get(rel, {})
        version = int(prev_meta.get("version", 0)) + 1

        path = self._write_file(rel, content)
        self._write_file(f"{self.HISTORY}/{rel}.v{version}", content)
        has_diff = False
        if previous is not None and previous != content:
            diff = "".join(
                difflib.unified_diff(
                    previous.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"{rel} v{version - 1}",
                    tofile=f"{rel} v{version}",
                )
            )
            self._write_file(f"{self.HISTORY}/{rel}.v{version}.diff", diff)
            has_diff = True

        self.metadata[rel] = {
            "version": version,
            "author": author,
            "stage": self.stage,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "has_diff": has_diff,
            "note": note,
            "chars": len(content),
        }
        self._save_metadata()
        return path

    def read_artifact(self, name: str) -> str:
        rel = _validate_name(name)
        path = os.path.join(self.workspace_dir, rel)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Artifact {rel} does not exist in {self.workspace_dir}")
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    def exists(self, name: str) -> bool:
        return os.path.exists(os.path.join(self.workspace_dir, _validate_name(name)))

    def list_artifacts(self) -> Dict[str, Dict[str, Any]]:
        return dict(self.metadata)

    def path(self, name: str) -> str:
        return os.path.join(self.workspace_dir, _validate_name(name))
