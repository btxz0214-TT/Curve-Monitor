#!/usr/bin/env python3
"""
Queue a deploy on AI Builders (POST /v1/deployments).

Docs: https://space.ai-builders.com/backend/openapi.json

Requires:
  - Bearer token: export AI_BUILDER_TOKEN or SUPER_MIND_API_KEY (student portal key).
  - deploy-config.json (copy from deploy-config.example.json): repo_url, service_name, branch, env_vars.

Optional:
  --merge-dotenv  Merge CRON_SECRET, RESEND_*, WEEKLY_DIGEST_TO from .env into env_vars (max 20 keys total).

env_vars are forwarded to Koyeb only; the platform does not store them (see OpenAPI DeploymentCreateRequest).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

BASE = Path(__file__).resolve().parent
API = "https://space.ai-builders.com/backend/v1/deployments"
CONFIG_PATH = BASE / "deploy-config.json"
EXAMPLE_PATH = BASE / "deploy-config.example.json"

# Keys pulled from .env when using --merge-dotenv (weekly digest + optional from address).
DOTENV_MERGE_KEYS = (
    "CRON_SECRET",
    "RESEND_API_KEY",
    "WEEKLY_DIGEST_TO",
    "RESEND_FROM",
)


def main() -> None:
    p = argparse.ArgumentParser(description="Deploy Phase C to AI Builders (Koyeb).")
    p.add_argument(
        "--merge-dotenv",
        action="store_true",
        help="Fill env_vars from .env for keys: " + ", ".join(DOTENV_MERGE_KEYS),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print JSON body only; do not POST.",
    )
    args = p.parse_args()

    if not CONFIG_PATH.is_file():
        print(f"Missing {CONFIG_PATH.name}. Copy {EXAMPLE_PATH.name} and edit:", file=sys.stderr)
        print(f"  cp {EXAMPLE_PATH.name} {CONFIG_PATH.name}", file=sys.stderr)
        sys.exit(1)

    token = (os.getenv("AI_BUILDER_TOKEN") or os.getenv("SUPER_MIND_API_KEY") or "").strip()
    if not token and not args.dry_run:
        print("Set AI_BUILDER_TOKEN or SUPER_MIND_API_KEY in the environment.", file=sys.stderr)
        sys.exit(1)

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    repo_url = (cfg.get("repo_url") or "").strip()
    service_name = (cfg.get("service_name") or "").strip()
    branch = (cfg.get("branch") or "").strip()
    env_vars: dict[str, str] = dict(cfg.get("env_vars") or {})

    if not repo_url or "YOUR_GITHUB" in repo_url:
        print("Edit deploy-config.json: set a real public repo_url.", file=sys.stderr)
        sys.exit(1)
    if not service_name or not branch:
        print("deploy-config.json must include service_name and branch.", file=sys.stderr)
        sys.exit(1)

    if args.merge_dotenv:
        dot = dotenv_values(BASE / ".env") or {}
        for k in DOTENV_MERGE_KEYS:
            v = (dot.get(k) or "").strip()
            if v:
                env_vars[k] = v

    if len(env_vars) > 20:
        print("env_vars exceeds 20 keys (Koyeb / platform limit).", file=sys.stderr)
        sys.exit(1)

    body = {
        "repo_url": repo_url,
        "service_name": service_name,
        "branch": branch,
        "port": 8000,
        "env_vars": env_vars,
        "streaming_log_timeout_seconds": 120,
    }

    if args.dry_run:
        safe = {**body, "env_vars": {k: "(set)" if v else "" for k, v in env_vars.items()}}
        print(json.dumps(safe, indent=2))
        return

    r = httpx.post(
        API,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=300.0,
    )
    print("HTTP", r.status_code)
    try:
        print(json.dumps(r.json(), indent=2, ensure_ascii=False))
    except Exception:
        print(r.text)


if __name__ == "__main__":
    main()
