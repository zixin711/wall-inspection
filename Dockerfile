# 私有化部署镜像。构建时需要一次外网（或内网 pip 源）；运行时只依赖内网的模型端点。
FROM python:3.11-slim

# opencv-python-headless 仍需要这几个运行库
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY web/ ./web/
COPY config/ ./config/
COPY reference/ ./reference/
COPY scripts/ ./scripts/

# 数据目录挂卷，镜像本身不留业务数据
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').read()" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
