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

## Weekly HIGH digest（推荐：GitHub + Resend，不占 Koyeb env）

不在部署里配 Resend：**GitHub Actions** 定时 **`POST /run-scan`**（唤醒实例），再用仓库里的 **`scripts/resend_weekly_high.py`** 和 **GitHub Secrets** 发信。见 **`.github/workflows/weekly-radar-digest.yml`**。

| GitHub Secret | 用途 |
|---------------|------|
| `RADAR_BASE_URL` | `https://你的服务.ai-builders.space`（不要末尾 `/`）；若曾用旧名，可改用 Secret **`RADAR_URL`**（二选一） |
| `RESEND_API_KEY` | [resend.com](https://resend.com) |
| `WEEKLY_DIGEST_TO` | 收件人 |
| `RESEND_FROM` | 可选 |
| `RUN_SCAN_SECRET` | 可选；若 Koyeb 上设置了同名 env，这里填相同值 |

**部署端（可选）** 只加 **`RUN_SCAN_SECRET`**：一键部署若只能配少量变量，就只配这个；不配则 `/run-scan` 公开（适合作业演示，公网建议加锁）。

**本地试跑扫描**（不发邮件）：

```bash
curl -X POST "http://127.0.0.1:8001/run-scan" -H "Accept: application/json" --max-time 900
```

## AI Builders 部署（Koyeb）

平台 API 见 [OpenAPI — deployments](https://space.ai-builders.com/backend/openapi.json)：`POST /v1/deployments` 可带 **`env_vars`**（字符串键值对，最多 20 个），由平台**转发到容器**，不在平台数据库长期保存（见规范里的 *Stateless Design*）。

### 两个部署槽位（常见坑）

每个学生通常只有 **2 个并发服务**（`GET /v1/deployments` 里的 `limit`）。若有一个服务一直处于 **`UNHEALTHY`**（例如第一次用新 `service_name` 部署失败），它仍会**占一个槽位**，且平台**没有**公开的删除接口，第二个名字可能永远无法在 Koyeb 上真正建起来。

**可行做法：** 把本项目的 GitHub 仓库 **重新部署到已有 HEALTHY 服务的 `service_name` 上**（例如你之前跑通过的 `btxz-chat`），即用同一 `service_name` 再发一次 `POST /v1/deployments`，`repo_url` 指向 **Curve-Monitor**。这样会**替换**该 URL 上原来的应用（原 Chat 需以后自己再部署回去）。

`deploy-config.example.json` 里默认把 `service_name` 设为 **`btxz-chat`** 就是为了走这条“占槽复用”路径。

### 重要：`btxz-chat` 实际从哪个 GitHub 构建？

平台侧 **`btxz-chat` 的 Koyeb 构建会一直使用 `https://github.com/btxz0214-TT/Chat` 仓库**（与部署 API 里填的 `repo_url` 可能不一致——我们拉过 **build log**，里面仍是 Chat 旧 Dockerfile 的 `COPY chat_proxy.py …`）。

因此要让线上出现 **Curve Monitor / Radar**，需要把 **与 Curve-Monitor 相同的代码推到 Chat 仓库的 `main`**（本仓库已用分支 **`backup/phase-a-chat-before-radar`** 备份了原 Chat），且 **`deploy-config.json` 的 `repo_url` 使用 Chat 仓库**后再发部署。

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
   `--merge-dotenv` 会从本目录 `.env` 合并可选的 `RUN_SCAN_SECRET` 到 `env_vars`（Resend 等放在 GitHub Secrets，不必塞进 Koyeb）。
4. 返回 **202** 后按响应说明轮询 `GET /v1/deployments/{service_name}`，约 5–10 分钟；公网地址一般为 `https://<service_name>.ai-builders.space`。

**说明：** 部署后平台会注入 **`AI_BUILDER_TOKEN`**；本应用已支持用它作为调用 Space 后端的密钥（与本地 `SUPER_MIND_API_KEY` 二选一）。
