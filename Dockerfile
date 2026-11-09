# Образ сервиса. python:3.12-slim — тег проверен на Docker Hub 2026-10-22.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY requirements-lock-linux.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements-lock-linux.txt

COPY app ./app
COPY main.py verify_install.py ./

# БД (SQLite) хранится в томе /srv/data
VOLUME ["/srv/data"]
EXPOSE 8080
CMD ["python", "main.py", "run"]
