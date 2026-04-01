FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Match the layout pattern used by the working btxz-chat image (selective COPY, not COPY . .).
COPY main.py background.md ./
COPY static ./static/

EXPOSE 8000

# Same CMD style as https://github.com/btxz0214-TT/Chat (proven HEALTHY on AI Builders).
CMD sh -c 'uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}'
