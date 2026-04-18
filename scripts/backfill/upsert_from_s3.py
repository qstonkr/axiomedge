#!/usr/bin/env python3
"""Phase 2: Read vectorized JSONL from S3 → upsert to local Qdrant.

Lightweight script for local execution. No ONNX needed — vectors are
already computed by Phase 1 (K8s pods).

Usage:
    python upsert_from_s3.py [--qdrant-url localhost:6333] [--collection kb_itops_general]

Env vars:
    QDRANT_URL       Qdrant endpoint (default: localhost:6333)
    COLLECTION_NAME  Collection name (default: kb_itops_general)
    S3_DATA_BUCKET   S3 bucket (default: gs-retail-svc-dev-miso-files)
    S3_OUTPUT_PREFIX Embedded JSONL prefix (default: knowledge/embedded/itops/)
    UPSERT_BATCH_SIZE Points per upsert (default: 200)
    AWS_PROFILE      AWS profile (default: <your-aws-profile>)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from typing import Any

import boto3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("upsert_from_s3")

DENSE_DIM = 1024


def list_embedded_files(s3_client: Any, bucket: str, prefix: str) -> list[str]:
    """List all *_embedded.jsonl files under prefix."""
    keys: list[str] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith("_embedded.jsonl"):
                keys.append(obj["Key"])
    keys.sort()
    return keys


def ensure_collection(client: Any, collection: str) -> None:
    """Create collection if not exists."""
    from qdrant_client.models import Distance, SparseVectorParams, VectorParams

    collections = [c.name for c in client.get_collections().collections]
    if collection in collections:
        logger.info("Collection %s already exists", collection)
        return

    logger.info("Creating collection %s ...", collection)
    client.create_collection(
        collection_name=collection,
        vectors_config={
            "bge_dense": VectorParams(size=DENSE_DIM, distance=Distance.COSINE),
        },
        sparse_vectors_config={
            "bge_sparse": SparseVectorParams(),
        },
    )
    client.create_payload_index(
        collection_name=collection,
        field_name="document_name",
        field_schema="keyword",
    )
    logger.info("Collection %s created", collection)


def upsert_file(
    s3_client: Any,
    qdrant_client: Any,
    bucket: str,
    key: str,
    collection: str,
    batch_size: int,
) -> tuple[int, int]:
    """Download embedded JSONL and upsert to Qdrant. Returns (upserted, errors)."""
    from qdrant_client.models import PointStruct, SparseVector

    fname = key.rsplit("/", 1)[-1]
    logger.info("Processing %s ...", fname)

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        tmp_path = tmp.name
        s3_client.download_file(bucket, key, tmp_path)

    size_mb = os.path.getsize(tmp_path) / (1024 * 1024)
    logger.info("  Downloaded %.1f MB", size_mb)

    points: list[Any] = []
    upserted = 0
    errors = 0

    try:
        with open(tmp_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    errors += 1
                    continue

                sparse = record["sparse_vector"]
                indices = sorted(int(k) for k in sparse.keys())
                values = [sparse[str(k)] for k in indices]

                points.append(PointStruct(
                    id=record["id"],
                    vector={
                        "bge_dense": record["dense_vector"],
                        "bge_sparse": SparseVector(indices=indices, values=values),
                    },
                    payload=record["payload"],
                ))

                if len(points) >= batch_size:
                    qdrant_client.upsert(
                        collection_name=collection, points=points, wait=True,
                    )
                    upserted += len(points)
                    points = []

        if points:
            qdrant_client.upsert(
                collection_name=collection, points=points, wait=True,
            )
            upserted += len(points)

    finally:
        os.unlink(tmp_path)

    logger.info("  %s: %d upserted, %d errors", fname, upserted, errors)
    return upserted, errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2: S3 vectorized JSONL → local Qdrant")
    parser.add_argument("--qdrant-url", default=os.getenv("QDRANT_URL", "localhost:6333"))
    parser.add_argument("--collection", default=os.getenv("COLLECTION_NAME", "kb_itops_general"))
    parser.add_argument("--bucket", default=os.getenv("S3_DATA_BUCKET", "gs-retail-svc-dev-miso-files"))
    parser.add_argument("--prefix", default=os.getenv("S3_OUTPUT_PREFIX", "knowledge/embedded/itops/"))
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("UPSERT_BATCH_SIZE", "200")))
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE", ""))
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Phase 2: Upsert from S3 to local Qdrant")
    logger.info("  Qdrant: %s / %s", args.qdrant_url, args.collection)
    logger.info("  Source: s3://%s/%s", args.bucket, args.prefix)
    logger.info("=" * 60)

    # S3 client with profile
    session = boto3.Session(profile_name=args.profile)
    s3_client = session.client("s3")

    # Qdrant client
    from qdrant_client import QdrantClient

    if ":" in args.qdrant_url:
        host, port = args.qdrant_url.rsplit(":", 1)
        qclient = QdrantClient(host=host, port=int(port), timeout=120)
    else:
        qclient = QdrantClient(host=args.qdrant_url, port=6333, timeout=120)

    ensure_collection(qclient, args.collection)

    # List embedded files
    keys = list_embedded_files(s3_client, args.bucket, args.prefix)
    if not keys:
        logger.error("No embedded JSONL files found at s3://%s/%s", args.bucket, args.prefix)
        sys.exit(1)
    logger.info("Found %d embedded files", len(keys))

    total_upserted = 0
    total_errors = 0
    t0 = time.monotonic()

    for key in keys:
        upserted, errors = upsert_file(
            s3_client, qclient, args.bucket, key, args.collection, args.batch_size,
        )
        total_upserted += upserted
        total_errors += errors

    elapsed = time.monotonic() - t0

    logger.info("=" * 60)
    logger.info("COMPLETE")
    logger.info("  Total upserted: %d", total_upserted)
    logger.info("  Total errors: %d", total_errors)
    logger.info("  Time: %.1f min", elapsed / 60)
    logger.info("=" * 60)

    if total_errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
