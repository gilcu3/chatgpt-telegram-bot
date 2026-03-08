FROM python:3.9-alpine

ENV PYTHONFAULTHANDLER=1 \
     PYTHONUNBUFFERED=1 \
     PYTHONDONTWRITEBYTECODE=1 \
     PIP_DISABLE_PIP_VERSION_CHECK=on

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir

RUN adduser -D botuser
USER botuser

COPY bot/ bot/

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
  CMD python -c "import sys; sys.exit(0)"

CMD ["python", "bot/main.py"]
