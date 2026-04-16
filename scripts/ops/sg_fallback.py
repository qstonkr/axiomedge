#!/usr/bin/env python3
"""Add or remove a wider fallback CIDR on the TEI/PaddleOCR security group.

Why: the per-IP refresh script (`refresh_tei_access.py`) handles dynamic
home ISPs as long as AWS credentials are alive. During multi-hour ingestion
runs, two things can break that loop:

  1. Home ISP rotates the egress IP and the new IP is no longer covered by
     the auto rule.
  2. SSO credentials expire, blocking any further SG updates from this
     machine.

A /24 fallback on the same ISP block absorbs both failure modes for the
duration of the run. Tagged with description `jbkim-fallback-*` so cleanup
is scriptable and never collides with the precise auto rule.

Usage:
    uv run python scripts/sg_fallback.py add --cidr 27.122.140.0/24
    uv run python scripts/sg_fallback.py remove   # revokes all jbkim-fallback-*
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date

SG_ID = "sg-026e71d1b3b93c576"
PORTS: tuple[int, ...] = (8080, 8081, 8866)
DESC_PREFIX = "jbkim-fallback"
REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
PROFILE = os.environ.get("AWS_PROFILE", "jeongbeomkim")


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


def describe_permissions() -> list[dict]:
    out = aws("ec2", "describe-security-groups", "--group-ids", SG_ID)
    return json.loads(out)["SecurityGroups"][0]["IpPermissions"]


def add_fallback(cidr: str) -> int:
    if "/" not in cidr:
        print(f"CIDR must include a prefix length (e.g. {cidr}/24)", file=sys.stderr)
        return 2

    tag = f"{DESC_PREFIX}-{date.today().strftime('%Y%m%d')}"
    print(f"Adding fallback CIDR : {cidr}")
    print(f"Target SG            : {SG_ID}")
    print(f"Ports                : {', '.join(str(p) for p in PORTS)}")
    print(f"Rule description     : {tag}")
    print()

    permissions = describe_permissions()
    for port in PORTS:
        ranges: list[dict] = []
        for p in permissions:
            if p.get("FromPort") == port and p.get("ToPort") == port:
                ranges.extend(p.get("IpRanges", []))

        already = any(r["CidrIp"] == cidr for r in ranges)
        if already:
            print(f"  = port {port}: {cidr} already present, skipping")
            continue

        print(f"  + port {port}: adding {cidr}")
        aws(
            "ec2",
            "authorize-security-group-ingress",
            "--group-id",
            SG_ID,
            "--ip-permissions",
            json.dumps([{
                "IpProtocol": "tcp",
                "FromPort": port,
                "ToPort": port,
                "IpRanges": [{"CidrIp": cidr, "Description": tag}],
            }]),
        )

    print()
    print("Done. Cleanup later with: uv run python scripts/sg_fallback.py remove")
    return 0


def remove_fallback() -> int:
    print(f"Removing all rules tagged '{DESC_PREFIX}-*' from {SG_ID}")
    permissions = describe_permissions()
    removed = 0

    for port in PORTS:
        ranges: list[dict] = []
        for p in permissions:
            if p.get("FromPort") == port and p.get("ToPort") == port:
                ranges.extend(p.get("IpRanges", []))

        fallback_ranges = [
            r for r in ranges if DESC_PREFIX in (r.get("Description") or "")
        ]
        for r in fallback_ranges:
            cidr = r["CidrIp"]
            desc = r.get("Description", "")
            print(f"  - port {port}: revoking {cidr} ({desc})")
            aws(
                "ec2",
                "revoke-security-group-ingress",
                "--group-id",
                SG_ID,
                "--ip-permissions",
                json.dumps([{
                    "IpProtocol": "tcp",
                    "FromPort": port,
                    "ToPort": port,
                    "IpRanges": [{"CidrIp": cidr}],
                }]),
            )
            removed += 1

    if removed == 0:
        print("  (no jbkim-fallback-* rules found)")
    print()
    print(f"Removed {removed} rule(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Add a fallback CIDR")
    p_add.add_argument("--cidr", required=True, help="CIDR with prefix, e.g. 27.122.140.0/24")

    sub.add_parser("remove", help="Remove all jbkim-fallback-* rules")

    args = parser.parse_args()
    if args.cmd == "add":
        return add_fallback(args.cidr)
    if args.cmd == "remove":
        return remove_fallback()
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
