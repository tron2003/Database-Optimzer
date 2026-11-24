FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN pip install --no-cache-dir "psycopg[binary]==3.2.3"

WORKDIR /app
COPY generate.py /app/generate.py

CMD ["python", "-u", "/app/generate.py"]
