"""
Strategic Information Radar — FastAPI backend (Two-Stage Scan).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import BadRequestError, OpenAI
from pydantic import BaseModel, ConfigDict, Field

BASE_DIR = Path(__file__).resolve().parent
# Always load `.env` next to this file (uvicorn cwd may not be Phase C).
load_dotenv(BASE_DIR / ".env")

BACKGROUND_PATH = BASE_DIR / "background.md"
STATIC_DIR = BASE_DIR / "static"
JOBS_DIR = BASE_DIR / "job_store"
_JOB_STALE_SEC = 48 * 3600

MODEL = "supermind-agent-v1"
API_BASE = "https://space.ai-builders.com/backend/v1"
MAX_ARTICLE_CHARS = 50_000
HTTP_TIMEOUT = 30.0
# Many news sites return 403 for non-browser or bot-like clients.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
# Fallback: Jina Reader fetches and returns article text (helps with some bot blocks).
_JINA_READER_PREFIX = "https://r.jina.ai/"

APP_DESCRIPTION = """
**Strategic Information Radar** is an MVP that keeps you aligned with external news against a fixed strategy file.

### What it does
1. **Stage 1 — Broad scan** reads `background.md` (next to this app), calls the AI Builder chat API (`supermind-agent-v1`) to identify **3–5 strategic search areas** and **real article URLs** (the model may use web search).
2. **Stage 2 — Deep dive** downloads each URL, extracts readable text, and calls the model again to rate **strategic importance** (`High` / `Medium` / `Low`) with a one-line summary and short reasoning.

### Configuration
- **Environment:** `SUPER_MIND_API_KEY` in `.env` (AI Builder Student Portal; not hardcoded in source).
- **Upstream API:** OpenAI-compatible base URL `https://space.ai-builders.com/backend/v1` via the official **OpenAI Python SDK**.
- **Context file:** `background.md` defines your internal strategy; the scan is always interpreted against that text.

### Using this API
- **`GET /`** — Human-facing page with a **Start Scan** button (same workflow as `POST /run-scan`).
- **`POST /run-scan`** — Same pipeline for scripts or Swagger “Try it out”. **No request body.** Runs synchronously and can take **several minutes** (one broad call + one analysis per article URL).

### Weekly HIGH email (no secrets on the host)
On **AI Builders / Koyeb**, avoid stuffing mail API keys into `env_vars`. Use **`.github/workflows/weekly-radar-digest.yml`**: it starts **`POST /run-scan/async`** (returns immediately; scan runs in-process), **polls `GET /run-scan/jobs/{id}`** until done (avoids gateway **504** on long synchronous `/run-scan`), then sends mail via **Resend** from GitHub Secrets (`scripts/resend_weekly_high.py`).

Optional: set **`RUN_SCAN_SECRET`** on the deployed app and the same value in GitHub **`RUN_SCAN_SECRET`**; then callers must send header **`X-Run-Scan-Secret`**. The web UI can supply it in the optional field under the button. If unset, `/run-scan` stays open (fine for class demos; add the secret for a public URL).
"""

TAGS_METADATA = [
    {
        "name": "Web UI",
        "description": "Serves the browser UI (`index.html`) so you can run a scan without using Swagger.",
    },
    {
        "name": "Scan workflow",
        "description": "Endpoints that execute the two-stage scan against `background.md` and return structured JSON.",
    },
]


class BroadStructured(BaseModel):
    """Parsed JSON from Stage 1 (fields may vary slightly; these are guaranteed by the server after cleanup)."""

    areas: list[str] = Field(
        default_factory=list,
        description="3–5 strings describing themes or angles to watch (e.g. competitor AMMs, routing).",
    )
    rationale: str = Field(
        default="",
        description="Short explanation of why those areas matter relative to `background.md`.",
    )
    article_urls: list[str] = Field(
        default_factory=list,
        description="Unique http(s) URLs discovered in Stage 1; each is analyzed in Stage 2.",
    )


class BroadScanReport(BaseModel):
    """Stage 1 output: raw assistant text plus structured fields."""

    raw_model_response: str = Field(
        ...,
        description="Full text returned by the model before JSON parsing (useful for debugging).",
    )
    structured: BroadStructured = Field(
        ...,
        description="Extracted JSON: areas, rationale, and article_urls used for Stage 2.",
    )


class DeepDiveItem(BaseModel):
    """One Stage 2 evaluation per article URL."""

    source_url: str = Field(..., description="Article URL that was fetched and analyzed.")
    importance: str = Field(
        ...,
        description='Model rating: exactly one of "High", "Medium", "Low".',
    )
    summary: str = Field(..., description="One-sentence summary of the article in strategic terms.")
    reasoning: str = Field(
        ...,
        description="Brief justification for the importance rating vs. internal context.",
    )
    fetch_error: Optional[str] = Field(
        default=None,
        description="If the page could not be downloaded or parsed, an error message; otherwise null.",
    )


class RunScanResponse(BaseModel):
    """Complete response from `POST /run-scan`: broad survey plus per-URL analysis."""

    broad_scan_report: BroadScanReport = Field(
        ...,
        description="Broad Scan Report: strategic areas and candidate article URLs from Stage 1.",
    )
    deep_dive_report: list[DeepDiveItem] = Field(
        default_factory=list,
        description="Deep Dive Report: one object per URL from Stage 1 (order matches processing order).",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "broad_scan_report": {
                        "raw_model_response": '{"areas": [...], "article_urls": [...]}',
                        "structured": {
                            "areas": [
                                "Stable-asset AMM launches",
                                "DEX aggregation / routing",
                            ],
                            "rationale": "Matches Curve-adjacent themes in background.md.",
                            "article_urls": ["https://example.com/news/amm-launch"],
                        },
                    },
                    "deep_dive_report": [
                        {
                            "source_url": "https://example.com/news/amm-launch",
                            "importance": "High",
                            "summary": "New stable pool design competes on slippage with Curve-style pools.",
                            "reasoning": "Directly matches stable-asset AMM focus in background.",
                            "fetch_error": None,
                        }
                    ],
                }
            ]
        }
    )


class AsyncScanQueued(BaseModel):
    """Immediate response from `POST /run-scan/async` before the scan finishes."""

    job_id: str = Field(..., description="Opaque id for `GET /run-scan/jobs/{job_id}`.")
    status: str = Field(default="pending", description="Always `pending` until the job record updates.")
    poll_path: str = Field(..., description="Relative URL to poll for the final `RunScanResponse`.")


app = FastAPI(
    title="Strategic Information Radar",
    description=APP_DESCRIPTION,
    version="0.1.0",
    openapi_tags=TAGS_METADATA,
    contact={
        "name": "AI Builder Student Portal",
        "url": "https://space.ai-builders.com/backend/openapi.json",
    },
    license_info={"name": "Educational / MVP"},
)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static_assets")


def get_client() -> OpenAI:
    # Local dev: SUPER_MIND_API_KEY in .env. AI Builders deploy: platform injects AI_BUILDER_TOKEN.
    key = (os.getenv("SUPER_MIND_API_KEY") or os.getenv("AI_BUILDER_TOKEN") or "").strip()
    if not key:
        raise HTTPException(
            status_code=500,
            detail="Set SUPER_MIND_API_KEY (local .env) or rely on AI_BUILDER_TOKEN (AI Builders deploy).",
        )
    return OpenAI(api_key=key, base_url=API_BASE)


def read_background() -> str:
    if not BACKGROUND_PATH.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"background.md not found at {BACKGROUND_PATH}",
        )
    return BACKGROUND_PATH.read_text(encoding="utf-8").strip()


def extract_json_object(text: str) -> dict:
    """Parse a JSON object from model output (handles optional markdown fences)."""
    if not text:
        raise ValueError("Empty model response")
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
    if fence:
        t = fence.group(1).strip()
    start = t.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model response")
    try:
        obj, _ = json.JSONDecoder().raw_decode(t, start)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in model response: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError("Model JSON root must be an object, not an array or primitive")
    return obj


def _content_part_to_text(part: object) -> str:
    """Best-effort text from one chat completion content part (dict, SDK model, or str)."""
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        typ = part.get("type")
        if typ == "refusal" and part.get("refusal"):
            raise HTTPException(
                status_code=502,
                detail=f"Model refused: {part.get('refusal')}",
            )
        t = part.get("text")
        if isinstance(t, str) and t:
            return t
        for k in ("content", "value", "output", "message"):
            v = part.get(k)
            if isinstance(v, str) and v.strip():
                return v
        return ""
    model_dump = getattr(part, "model_dump", None)
    if callable(model_dump):
        d = model_dump()
        if isinstance(d, dict):
            if d.get("type") == "refusal" and d.get("refusal"):
                raise HTTPException(status_code=502, detail=f"Model refused: {d['refusal']}")
            t = d.get("text")
            if isinstance(t, str) and t:
                return t
    t = getattr(part, "text", None)
    return (t or "") if isinstance(t, str) else ""


def _assistant_message_text(choice) -> str:
    """Extract visible text from a chat completion choice; raise on refusal."""
    msg = choice.message
    refusal = getattr(msg, "refusal", None)
    if refusal:
        raise HTTPException(status_code=502, detail=f"Model refused: {refusal}")
    content = msg.content
    if isinstance(content, str):
        if content.strip():
            return content.strip()
    elif isinstance(content, list):
        pieces: list[str] = []
        for p in content:
            pieces.append(_content_part_to_text(p))
        joined = "".join(pieces).strip()
        if joined:
            return joined

    # Gateways / reasoning models may put text in extra fields (ChatCompletionMessage has extra='allow')
    dump: dict = {}
    try:
        dump = msg.model_dump(mode="python")
    except Exception:
        pass
    for key in ("reasoning_content", "reasoning", "thinking", "output_text", "output"):
        v = dump.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    extra = getattr(msg, "__pydantic_extra__", None)
    if isinstance(extra, dict):
        for key in ("reasoning_content", "reasoning", "thinking"):
            v = extra.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()

    return ""


def chat_completion(
    client: OpenAI,
    messages: list[dict],
    max_tokens: int = 4096,
    *,
    retry_tool_choice_none: bool = False,
) -> str:
    """
    Call the chat API. Do not use response_format=json_object with supermind-agent-v1:
    the multi-tool orchestrator often returns finish_reason=stop with empty content.
    """
    last_detail = ""
    nudge = {
        "role": "user",
        "content": (
            "Your last completion had no visible assistant text in this API. "
            "Reply with a single text message containing only the JSON we requested. "
            "Do not use tools. Do not wrap in markdown code fences."
        ),
    }

    for attempt_idx in range(3):
        msgs: list[dict] = list(messages)
        if attempt_idx == 2:
            msgs = msgs + [nudge]

        ex: dict = {"temperature": 0.5 if attempt_idx == 0 else 0.2}
        if attempt_idx == 1 and retry_tool_choice_none:
            ex["tool_choice"] = "none"
        if attempt_idx == 2:
            ex["tool_choice"] = "none"

        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=msgs,
                max_tokens=max_tokens,
                **ex,
            )
        except BadRequestError:
            if ex.pop("tool_choice", None) is not None:
                resp = client.chat.completions.create(
                    model=MODEL,
                    messages=msgs,
                    max_tokens=max_tokens,
                    **ex,
                )
            else:
                raise
        choice = resp.choices[0]
        text = _assistant_message_text(choice)
        if text:
            return text
        usage = resp.usage
        ct = getattr(usage, "completion_tokens", None) if usage else None
        last_detail = (
            f"finish_reason={choice.finish_reason!r}, completion_tokens={ct!r}; "
            f"tool_calls={bool(getattr(choice.message, 'tool_calls', None))}"
        )

    raise HTTPException(
        status_code=502,
        detail=(
            "Upstream model returned no message text after retries. "
            f"{last_detail} "
            "If this persists, the portal may be overloaded or the orchestrator "
            "returned an empty completion."
        ),
    )


def _browser_request_headers(target_url: str) -> dict[str, str]:
    parsed = urlparse(target_url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""
    h: dict[str, str] = {
        "User-Agent": _BROWSER_UA,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
    }
    if origin:
        h["Referer"] = origin + "/"
    return h


def _fetch_via_jina_reader(client: httpx.Client, url: str) -> httpx.Response:
    """Proxy fetch through Jina Reader (plain/markdown body; bypasses some 403s)."""
    reader_url = _JINA_READER_PREFIX + url
    r = client.get(reader_url, headers=_browser_request_headers(reader_url))
    r.raise_for_status()
    return r


def _get_article_http_response(client: httpx.Client, url: str) -> tuple[httpx.Response, bool]:
    """
    GET the article. Returns (response, used_reader_fallback).
    Retries via Jina Reader on common bot-block / overload statuses or connection errors.
    """
    headers = _browser_request_headers(url)
    try:
        r = client.get(url, headers=headers)
        r.raise_for_status()
        return r, False
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code in (401, 403, 429, 451, 503):
            return _fetch_via_jina_reader(client, url), True
        raise
    except httpx.RequestError:
        return _fetch_via_jina_reader(client, url), True


def fetch_article_text(url: str) -> str:
    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        r, via_reader = _get_article_http_response(client, url)
    ctype = r.headers.get("content-type", "").lower()
    body = r.text or ""

    if via_reader or "text/html" not in ctype and "application/xhtml" not in ctype:
        return body[:MAX_ARTICLE_CHARS] if body else ""

    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    lines = [ln for ln in (line.strip() for line in text.splitlines()) if ln]
    out = "\n".join(lines)
    return out[:MAX_ARTICLE_CHARS]


def run_broad_scan(client: OpenAI, background: str) -> dict:
    system = (
        "You are a strategic research assistant. Use web search when needed to find "
        "current, real news articles. Always respond with valid JSON only, no markdown."
    )
    user = f"""Strategic background:
---
{background}
---

Based on the following strategic background, survey 3-5 relevant areas for a broad news search.
Use your tools to find recent, credible news articles (each must be a real http(s) URL).

Rules for article_urls:
- Only include URLs that appear in search/tool results or that you can confirm exist; do not invent paths (404s break the pipeline).
- Prefer major publishers and stable article pages (avoid paywalled-only or auth-only links when possible).
- Each entry must be the full https URL of a specific article, not a site homepage.

Return a single JSON object with exactly these keys:
- "areas": array of strings, each describing one search/relevance area (3-5 items)
- "rationale": string, brief explanation of why these areas matter for the background
- "article_urls": array of 3-8 unique strings, each a full https URL to a specific news or analysis article (not homepages only)

Do not include any text outside the JSON object."""
    try:
        raw = chat_completion(
            client,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=8192,
        )
        data = extract_json_object(raw)
    except HTTPException:
        raise
    except (json.JSONDecodeError, ValueError) as e:
        preview = raw[:1200] if isinstance(raw, str) else ""
        raise HTTPException(
            status_code=502,
            detail=(
                f"Stage 1 model output could not be parsed as JSON ({e}). "
                f"First 1200 chars of response: {preview!r}"
            ),
        ) from e
    urls = data.get("article_urls") or []
    if not isinstance(urls, list):
        urls = []
    clean_urls = []
    seen = set()
    for u in urls:
        if not isinstance(u, str):
            continue
        u = u.strip()
        if u.startswith("http://") or u.startswith("https://"):
            if u not in seen:
                seen.add(u)
                clean_urls.append(u)
    data["article_urls"] = clean_urls
    return {
        "raw_model_response": raw,
        "structured": data,
    }


def run_deep_dive_item(
    client: OpenAI, background: str, url: str, article_text: str
) -> dict:
    system = (
        "You evaluate external news against internal strategy. "
        "Reply with a single JSON object only, no markdown fences."
    )
    user = f"""Internal Context:
{background}

External Information (from {url}):
---
{article_text}
---

Analytical Task: Based on the internal context, evaluate the strategic importance of the external information.
Importance measures **competitive or structural impact on the positions and goals described above**, not mere topical overlap. If the internal context names assets or protocols we **hold**, treat generic reviews or explainers **about those names** as **Low** unless they report **new** material risk or a **new** competitive angle. Reserve **High** for competitor moves, substitutes, share or liquidity shifts, or events that directly challenge the stated strategy.

Return a single JSON object with exactly these fields:
- "importance": one of the strings "High", "Medium", or "Low"
- "summary": one sentence
- "reasoning": brief explanation for your importance rating"""
    raw = chat_completion(
        client,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=2048,
        retry_tool_choice_none=True,
    )
    try:
        obj = extract_json_object(raw)
    except (json.JSONDecodeError, ValueError):
        obj = {
            "importance": "Low",
            "summary": "Could not parse model output.",
            "reasoning": raw[:500] if raw else "Empty response",
        }
    imp = obj.get("importance", "Low")
    if imp not in ("High", "Medium", "Low"):
        imp = "Low"
    return {
        "source_url": url,
        "importance": imp,
        "summary": str(obj.get("summary", "")),
        "reasoning": str(obj.get("reasoning", "")),
    }


def coerce_broad_structured(data: dict) -> BroadStructured:
    areas = data.get("areas")
    if not isinstance(areas, list):
        areas = []
    areas = [str(a) for a in areas]
    urls = data.get("article_urls")
    if not isinstance(urls, list):
        urls = []
    urls = [str(u) for u in urls]
    return BroadStructured(
        areas=areas,
        rationale=str(data.get("rationale", "")),
        article_urls=urls,
    )


def execute_run_scan() -> RunScanResponse:
    """Full two-stage scan; shared by `/run-scan` and `/run-scan/async`."""
    background = read_background()
    client = get_client()

    broad = run_broad_scan(client, background)
    structured = broad["structured"]
    urls = structured.get("article_urls") or []

    deep_dive: list[dict] = []
    for url in urls:
        try:
            text = fetch_article_text(url)
            if not text.strip():
                deep_dive.append(
                    {
                        "source_url": url,
                        "importance": "Low",
                        "summary": "No extractable text from URL.",
                        "reasoning": "Fetch or parse yielded empty content.",
                        "fetch_error": None,
                    }
                )
                continue
        except Exception as e:
            deep_dive.append(
                {
                    "source_url": url,
                    "importance": "Low",
                    "summary": "Failed to retrieve article content.",
                    "reasoning": str(e)[:300],
                    "fetch_error": str(e),
                }
            )
            continue
        item = run_deep_dive_item(client, background, url, text)
        deep_dive.append(item)

    report = BroadScanReport(
        raw_model_response=broad["raw_model_response"],
        structured=coerce_broad_structured(broad["structured"]),
    )
    items = [DeepDiveItem.model_validate(row) for row in deep_dive]
    return RunScanResponse(broad_scan_report=report, deep_dive_report=items)


def _verify_run_scan_secret(x_secret: Optional[str]) -> None:
    expected = (os.getenv("RUN_SCAN_SECRET") or "").strip()
    if not expected:
        return
    if not x_secret or x_secret != expected:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing X-Run-Scan-Secret header.",
        )


def _job_file(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _write_job_record(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _prune_stale_jobs() -> None:
    if not JOBS_DIR.is_dir():
        return
    cutoff = time.time() - _JOB_STALE_SEC
    for p in JOBS_DIR.glob("*.json"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
        except OSError:
            pass


def _async_scan_worker(job_id: str) -> None:
    path = _job_file(job_id)
    try:
        result = execute_run_scan()
        _write_job_record(
            path,
            {
                "status": "done",
                "result": result.model_dump(mode="json"),
            },
        )
    except HTTPException as e:
        d = e.detail
        if not isinstance(d, str):
            d = json.dumps(d, ensure_ascii=False)[:2000]
        _write_job_record(path, {"status": "error", "detail": d})
    except Exception as e:
        _write_job_record(path, {"status": "error", "detail": str(e)[:2000]})


@app.get(
    "/",
    tags=["Web UI"],
    summary="Open the Start Scan page",
    description=(
        "Returns `static/index.html`: a minimal UI with a **Start Scan** button that "
        "issues `POST /run-scan` in the browser and renders the JSON report."
    ),
    responses={
        200: {
            "description": "HTML document for the radar UI.",
            "content": {
                "text/html": {
                    "example": "<!DOCTYPE html><html>...</html>",
                }
            },
        },
        404: {
            "description": "`index.html` is missing under the `static/` directory.",
        },
    },
)
def serve_index():
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="index.html missing")
    return FileResponse(index)


@app.post(
    "/run-scan",
    response_model=RunScanResponse,
    tags=["Scan workflow"],
    summary="Run the full two-stage scan",
    description=f"""
Execute the **Strategic Information Radar** pipeline synchronously.

**Stage 1 — Broad scan**
- Loads **`background.md`** from `{BACKGROUND_PATH.name}` (same folder as the app).
- One chat completion on **`{MODEL}`** asking for strategic areas and real article URLs (model may use web search).

**Stage 2 — Deep dive**
- For each URL from Stage 1, the server **fetches** the page (HTTP, HTML text extraction) and sends the text plus `background.md` to the model again.
- Each item is scored **`High` / `Medium` / `Low`** with summary and reasoning.

**Request:** no body.

**Time:** often **several minutes** (network + multiple LLM calls).

**Optional lock:** if environment variable **`RUN_SCAN_SECRET`** is set, send header **`X-Run-Scan-Secret`** with the same value (GitHub Actions and the web UI optional field).

**Errors:** `500` if `SUPER_MIND_API_KEY` / `AI_BUILDER_TOKEN` is missing or `background.md` is missing. `401` if `RUN_SCAN_SECRET` is set and the header is wrong or missing.
""",
    responses={
        200: {"description": "Broad Scan Report and Deep Dive Report as JSON (see response schema)."},
        401: {"description": "`RUN_SCAN_SECRET` is set but header missing or invalid."},
        500: {
            "description": "Missing API key, missing `background.md`, or upstream model/API failure.",
        },
    },
)
def run_scan(
    x_run_scan_secret: Optional[str] = Header(None, alias="X-Run-Scan-Secret"),
) -> RunScanResponse:
    _verify_run_scan_secret(x_run_scan_secret)
    return execute_run_scan()


@app.post(
    "/run-scan/async",
    response_model=AsyncScanQueued,
    tags=["Scan workflow"],
    summary="Queue full scan (returns immediately)",
    description=f"""
Starts the same pipeline as **`POST /run-scan`** in a **background thread** and returns a **`job_id`**
immediately. Poll **`GET /run-scan/jobs/{{job_id}}`** until you receive **200** with the normal scan JSON
(or **500** if the scan failed).

Use this from **GitHub Actions** or other clients behind a **short gateway timeout** (e.g. **504** on
long synchronous `/run-scan`). Same optional **`RUN_SCAN_SECRET`** / **`X-Run-Scan-Secret`** as `/run-scan`.

Jobs are stored on local disk under `{JOBS_DIR.name}/` (single-instance; typical for this MVP).
""",
    responses={
        200: {"description": "Job accepted; scan is running."},
        401: {"description": "`RUN_SCAN_SECRET` is set but header missing or invalid."},
    },
)
def run_scan_async(
    x_run_scan_secret: Optional[str] = Header(None, alias="X-Run-Scan-Secret"),
) -> AsyncScanQueued:
    _verify_run_scan_secret(x_run_scan_secret)
    _prune_stale_jobs()
    job_id = uuid.uuid4().hex
    path = _job_file(job_id)
    _write_job_record(path, {"status": "pending"})
    t = threading.Thread(
        target=_async_scan_worker,
        args=(job_id,),
        name=f"async-scan-{job_id[:8]}",
        daemon=True,
    )
    t.start()
    return AsyncScanQueued(
        job_id=job_id,
        status="pending",
        poll_path=f"/run-scan/jobs/{job_id}",
    )


@app.get(
    "/run-scan/jobs/{job_id}",
    tags=["Scan workflow"],
    summary="Poll async scan job",
    description="""
Returns **202** with `{{"status":"pending"}}` while the scan runs; **200** with the same body shape as
`POST /run-scan` when finished; **404** if the id is unknown or expired (~48h).
""",
    responses={
        200: {"description": "Scan finished successfully.", "model": RunScanResponse},
        202: {"description": "Still running."},
        404: {"description": "Invalid or unknown job_id."},
        500: {"description": "Scan failed; `detail` explains."},
    },
)
def run_scan_job_status(job_id: str):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(status_code=404, detail="Invalid job_id")
    path = _job_file(job_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Unknown or expired job")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Corrupt job record")

    st = data.get("status")
    if st == "pending":
        return JSONResponse(status_code=202, content={"status": "pending"})
    if st == "error":
        raise HTTPException(status_code=500, detail=data.get("detail") or "Scan failed")
    if st == "done" and "result" in data:
        return RunScanResponse.model_validate(data["result"])
    raise HTTPException(status_code=500, detail="Invalid job record")
