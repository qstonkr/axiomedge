#!/usr/bin/env python3
"""Sync the developer's current egress IP to the TEI/PaddleOCR security group.

Why: TEI (BGE-M3 + reranker) on `i-0e50628fc7ecfc242` and PaddleOCR on
`i-09c72e77a614f1ea2` share `sg-026e71d1b3b93c576`, which gates access by
IP CIDR. Home ISPs hand out dynamic IPs, so manual SG edits happen too
often. This script detects the current egress IP, adds a `/32` rule tagged
`jbkim-auto-*` if missing, and revokes any stale `jbkim-auto-*` rules.

Usage:
    uv run python scripts/refresh_tei_access.py
    # or: make tei-refresh

Idempotent: safe to run repeatedly. Only touches rules carrying the
`jbkim-auto` description — never other tenants' rules.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import date

SG_ID = "sg-026e71d1b3b93c576"
PORTS: tuple[int, ...] = (8080, 8081, 8866)
DESC_PREFIX = "jbkim-auto"
REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
PROFILE = os.environ.get("AWS_PROFILE", "<your-aws-profile>")

HEALTH_TARGETS = [
    ("BGE embedding", "http://54.180.231.139:8080/health"),
    ("BGE reranker ", "http://54.180.231.139:8081/health"),
]


def aws(*args: str) -> str:
    env = {**os.environ, "AWS_PROFILE": PROFILE, "AWS_REGION": REGION}
    result = subprocess.run(
        ["aws", *args, "--output", "json"],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        print(f"[aws error] aws {' '.join(args)}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout


def current_egress_ip() -> str:
    with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=5) as r:
        ip = r.read().decode().strip()
    parts = ip.split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        raise RuntimeError(f"Bad egress IP response: {ip!r}")
    return ip


def describe_sg_permissions() -> list[dict]:
    out = aws("ec2", "describe-security-groups", "--group-ids", SG_ID)
    return json.loads(out)["SecurityGroups"][0]["IpPermissions"]


def authorize(port: int, cidr: str, description: str) -> None:
    perms = [
        {
            "IpProtocol": "tcp",
            "FromPort": port,
            "ToPort": port,
            "IpRanges": [{"CidrIp": cidr, "Description": description}],
        }
    ]
    aws(
        "ec2",
        "authorize-security-group-ingress",
        "--group-id",
        SG_ID,
        "--ip-permissions",
        json.dumps(perms),
    )


def revoke(port: int, cidr: str) -> None:
    perms = [
        {
            "IpProtocol": "tcp",
            "FromPort": port,
            "ToPort": port,
            "IpRanges": [{"CidrIp": cidr}],
        }
    ]
    aws(
        "ec2",
        "revoke-security-group-ingress",
        "--group-id",
        SG_ID,
        "--ip-permissions",
        json.dumps(perms),
    )


def http_health(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return f"{r.status}"
    except Exception as exc:  # noqa: BLE001
        return f"ERR {type(exc).__name__}: {exc}"


def main() -> int:
    ip = current_egress_ip()
    cidr = f"{ip}/32"
    tag = f"{DESC_PREFIX}-{date.today().strftime('%Y%m%d')}"
    print(f"Current egress IP : {cidr}")
    print(f"Target SG         : {SG_ID}")
    print(f"Ports             : {', '.join(str(p) for p in PORTS)}")
    print(f"Rule description  : {tag}")
    print()

    permissions = describe_sg_permissions()

    for port in PORTS:
        ranges: list[dict] = []
        for p in permissions:
            if p.get("FromPort") == port and p.get("ToPort") == port:
                ranges.extend(p.get("IpRanges", []))

        auto_ranges = [
            r for r in ranges if DESC_PREFIX in (r.get("Description") or "")
        ]
        has_current = any(r["CidrIp"] == cidr for r in auto_ranges)
        stale = [r for r in auto_ranges if r["CidrIp"] != cidr]

        if has_current:
            print(f"  = port {port}: {cidr} already present")
        else:
            print(f"  + port {port}: adding {cidr}")
            authorize(port, cidr, tag)

        for r in stale:
            old_cidr = r["CidrIp"]
            old_desc = r.get("Description", "")
            print(f"  - port {port}: revoking stale {old_cidr} ({old_desc})")
            revoke(port, old_cidr)

    print()
    print("Verifying TEI reachability...")
    all_ok = True
    for name, url in HEALTH_TARGETS:
        status = http_health(url)
        ok = status == "200"
        marker = "✓" if ok else "✗"
        print(f"  {marker} {name} {url} → {status}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print(f"OK. SG {SG_ID} allows {cidr} on {list(PORTS)}.")
        return 0
    print("WARN: SG updated but health check failed. Check EC2 / TEI service.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
