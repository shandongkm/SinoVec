FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash appuser

COPY requirements.txt .
RUN pip install --no-cache-dir --no-user -r requirements.txt

COPY memory_sinovec.py extract_memories_sinovec.py session_indexer_sinovec.py common.py ./
COPY fix-zhparser.sh /usr/local/bin/fix-zhparser.sh
RUN chmod +x /usr/local/bin/fix-zhparser.sh

RUN chown -R appuser:appuser /app

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:18793/health')" || exit 1

EXPOSE 18793

USER appuser

CMD ["python", "memory_sinovec.py", "serve", "--host", "0.0.0.0", "--port", "18793"]
