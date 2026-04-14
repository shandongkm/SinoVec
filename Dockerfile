FROM python:3.12-slim

WORKDIR /app

# 安装系统依赖（libpq-dev 用于 psycopg2）
RUN apt-get update && apt-get install -y \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装 Python 包
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制所有 Python 脚本（显式列出，避免遗漏）
COPY memory_layer.py extract_memories.py session_indexer.py ./

# 健康检查
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:18793/health')" || exit 1

EXPOSE 18793

CMD ["python", "memory_layer.py", "serve", "--host", "0.0.0.0", "--port", "18793"]
