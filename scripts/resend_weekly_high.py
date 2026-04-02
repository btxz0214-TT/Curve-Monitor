#!/usr/bin/env python3
"""
Build a plain-text HIGH-summary email from `POST /run-scan` JSON and send via Resend.

Used by GitHub Actions (secrets live in GitHub, not on Koyeb).

Env:
  RESEND_API_KEY   (required)
  WEEKLY_DIGEST_TO (required)
  RESEND_FROM      optional, default "Radar <onboarding@resend.dev>"
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def build_message(data: dict) -> tuple[str, str]:
    items = data.get("deep_dive_report") or []
    highs = [i for i in items if (i.get("importance") or "").strip() == "High"]
    lines = [
        "Strategic Information Radar — weekly HIGH importance summary",
        "",
        f"High-rated items: {len(highs)}",
        "",
    ]
    if not highs:
        lines.append("No items were rated High this run.")
    else:
        for n, item in enumerate(highs, start=1):
            lines.append(f"--- {n}. {item.get('source_url', '')}")
            lines.append(f"Summary: {item.get('summary', '')}")
            lines.append(f"Reasoning: {item.get('reasoning', '')}")
            if item.get("fetch_error"):
                lines.append(f"Fetch note: {item['fetch_error']}")
            lines.append("")
    broad = (data.get("broad_scan_report") or {}).get("structured") or {}
    areas = broad.get("areas") or []
    lines.append("--- Stage 1 (context)")
    lines.append("Areas: " + ("; ".join(str(a) for a in areas) if areas else "(none)"))
    lines.append(f"Rationale: {broad.get('rationale', '')}")
    subject = (
        f"[Radar] {len(highs)} HIGH risk item(s)"
        if highs
        else "[Radar] Weekly scan — no HIGH items"
    )
    return subject, "\n".join(lines)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: resend_weekly_high.py <run-scan-response.json>", file=sys.stderr)
        sys.exit(2)
    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    key = (os.getenv("RESEND_API_KEY") or "").strip()
    to_addr = (os.getenv("WEEKLY_DIGEST_TO") or "").strip()
    if not key or not to_addr:
        print("Missing RESEND_API_KEY or WEEKLY_DIGEST_TO", file=sys.stderr)
        sys.exit(1)

    subject, text = build_message(data)
    from_addr = (os.getenv("RESEND_FROM") or "Radar <onboarding@resend.dev>").strip()
    payload = json.dumps(
        {"from": from_addr, "to": [to_addr], "subject": subject, "text": text}
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(resp.status, body)
            if resp.status >= 400:
                sys.exit(1)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        print(f"Resend HTTP {e.code}: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
