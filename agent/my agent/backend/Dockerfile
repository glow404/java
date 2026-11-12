# API 和 RQ 共用运行镜像，输入源码/依赖，输出可独立启动的 Python 服务。
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=2
WORKDIR /app
COPY pyproject.toml requirements.lock ./
# 第一版使用 Docling 的无模型文本 PDF 路径，无需下载 OCR/深度学习权重。
RUN --mount=type=cache,target=/root/.cache/pip python -c "import tomllib; p=tomllib.load(open('pyproject.toml','rb')); open('/tmp/requirements.txt','w').write('\n'.join(p['project']['dependencies']+p['project']['optional-dependencies']['test']))" && \
    (pip install --retries 10 --timeout 120 -c requirements.lock -r /tmp/requirements.txt || pip install --retries 10 --timeout 120 -c requirements.lock -r /tmp/requirements.txt || pip install --retries 10 --timeout 120 -c requirements.lock -r /tmp/requirements.txt)
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY tests ./tests
RUN useradd --uid 10001 --create-home vibe && mkdir -p /data/uploads /data/test-uploads && chown -R vibe:vibe /data /app
USER vibe
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
