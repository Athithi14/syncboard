FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app

COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY server/main.py server/main.py
COPY client client

RUN useradd -m app && mkdir -p /app/server/data && chown -R app /app
USER app
WORKDIR /app/server
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:'+os.environ['PORT']+'/health')"

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
