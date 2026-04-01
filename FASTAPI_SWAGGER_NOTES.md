# FastAPI & Swagger UI — quick reference

Use this when you forget how the pieces fit together.

## Names

| Term | What it is |
|------|------------|
| **FastAPI** | The Python web framework. You define routes (`@app.get`, `@app.post`, etc.); it runs as an HTTP API (usually with **Uvicorn**). |
| **OpenAPI** | A standard JSON/YAML **description** of your API (paths, methods, request/response shapes). Not a UI—just data. |
| **`/openapi.json`** | Where FastAPI **publishes** that description for this app. Other tools read this file. |
| **Swagger UI** | A **ready-made web page** that reads an OpenAPI file and shows interactive docs (“Try it out”). FastAPI mounts it for you. |
| **`/docs`** | Default URL for **Swagger UI** in FastAPI. |
| **ReDoc** | Another documentation **viewer** (cleaner read-only layout). Same OpenAPI source, different skin. |
| **`/redoc`** | Default URL for ReDoc in FastAPI. |

**One-line story:** FastAPI implements your API **and** generates OpenAPI. Swagger UI (and ReDoc) are **viewers** that display that spec—you did not hand-write those pages.

## Defaults in FastAPI

When you do `app = FastAPI(...)`, you normally get:

- `GET /openapi.json` — raw OpenAPI spec  
- `GET /docs` — Swagger UI  
- `GET /redoc` — ReDoc  

To turn off the built-in doc pages (e.g. in production):

```python
app = FastAPI(docs_url=None, redoc_url=None)
```

(`openapi.json` can also be disabled with `openapi_url=None` if you need that.)

## This project (Strategic Information Radar)

- **App entry:** `main.py` → `app = FastAPI(...)`  
- **Your UI:** `GET /` serves `static/index.html` (the “Start Scan” page)—this is **separate** from Swagger UI.  
- **API docs:** `http://127.0.0.1:<port>/docs` — try `POST /run-scan` here without using the HTML front-end.  
- **Richer docs:** Descriptions, tags, and response models in `main.py` are what make Swagger UI show explanations and schemas (OpenAPI metadata).

## Running locally

```bash
cd "AI Architects/Phase C"
uvicorn main:app --reload --host 127.0.0.1 --port 8001
```

Then open `/docs` or `/` as needed.

## Weekly HIGH digest (cron)

Endpoint: **`POST /internal/weekly-high-digest`**

| Env var | Purpose |
|--------|---------|
| `CRON_SECRET` | Long random string; caller must send header `X-Cron-Secret` with the same value. |
| **Option A — Resend (no Gmail app password)** | |
| `RESEND_API_KEY` | API key from [resend.com](https://resend.com). If set, email is sent via Resend instead of SMTP. |
| `WEEKLY_DIGEST_TO` | Recipient address (e.g. your Gmail). Required for Resend. |
| `RESEND_FROM` | Optional. Default `Radar <onboarding@resend.dev>` (Resend test sender). |
| **Option B — SMTP** | |
| `SMTP_HOST` | e.g. `smtp.gmail.com` |
| `SMTP_PORT` | `587` (STARTTLS) or `465` (SSL) |
| `SMTP_USER` | Login user (e.g. Gmail address). |
| `SMTP_PASSWORD` | Often an app password (Google Workspace / 2FA) — **not available on all accounts**. |
| `WEEKLY_DIGEST_TO` | Optional with SMTP; defaults to `SMTP_USER`. |

**Local test** (replace values):

```bash
curl -X POST "http://127.0.0.1:8001/internal/weekly-high-digest" \
  -H "X-Cron-Secret: YOUR_CRON_SECRET" \
  --max-time 900
```

**Deployed:** use your public base URL instead of `127.0.0.1`. Set client timeout to **several minutes** (same as `/run-scan`).

**GitHub Actions** (repo → Settings → Secrets): store `RADAR_URL` (full URL to the endpoint) and `RADAR_CRON_SECRET`. Example workflow:

```yaml
on:
  schedule: [{ cron: "0 9 * * 1" }]   # Mondays 09:00 UTC
  workflow_dispatch: {}
jobs:
  digest:
    runs-on: ubuntu-latest
    steps:
      - run: |
          curl -fsS -X POST "$RADAR_URL" \
            -H "X-Cron-Secret: $RADAR_CRON_SECRET" \
            --max-time 900
        env:
          RADAR_URL: ${{ secrets.RADAR_URL }}
          RADAR_CRON_SECRET: ${{ secrets.RADAR_CRON_SECRET }}
```

## AI Builders 部署（Koyeb）

平台 API 见 [OpenAPI — deployments](https://space.ai-builders.com/backend/openapi.json)：`POST /v1/deployments` 可带 **`env_vars`**（字符串键值对，最多 20 个），由平台**转发到容器**，不在平台数据库长期保存（见规范里的 *Stateless Design*）。

本目录已包含：

| 文件 | 作用 |
|------|------|
| `Dockerfile` | 构建镜像；`CMD` 使用 `${PORT:-8000}` |
| `.dockerignore` | 排除 `.env`、`deploy-config.json` 等 |
| `deploy-config.example.json` | 部署参数模板 |
| `deploy_to_ai_builders.py` | 调用部署 API |

**流程简述**

1. 在 GitHub 建**公开**仓库，把 **Phase C 目录下的文件作为仓库根目录**（`main.py`、`Dockerfile`、`static/`、`background.md` 等在根目录），`push` 到 `main`（或你的分支）。
2. `cp deploy-config.example.json deploy-config.json`，编辑其中的 `repo_url`、`service_name`（小写字母数字连字符，3–32 字符）、`branch`。
3. 在本机终端（已安装依赖 `httpx`、`python-dotenv`）：
   ```bash
   export AI_BUILDER_TOKEN=你的学生门户_API_Key   # 或与 .env 里相同的密钥
   python deploy_to_ai_builders.py --merge-dotenv
   ```
   `--merge-dotenv` 会从本目录 `.env` 合并 `CRON_SECRET`、`RESEND_*`、`WEEKLY_DIGEST_TO` 到请求的 `env_vars`（无需把密钥写进 `deploy-config.json`）。
4. 返回 **202** 后按响应说明轮询 `GET /v1/deployments/{service_name}`，约 5–10 分钟；公网地址一般为 `https://<service_name>.ai-builders.space`。

**说明：** 部署后平台会注入 **`AI_BUILDER_TOKEN`**；本应用已支持用它作为调用 Space 后端的密钥（与本地 `SUPER_MIND_API_KEY` 二选一）。
