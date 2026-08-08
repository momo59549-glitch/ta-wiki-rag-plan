FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps ./apps
COPY scripts ./scripts
RUN pip install --no-cache-dir ".[orchestration,ui,knowledge,research]"
ENV PYTHONPATH=/app
CMD ["python", "-m", "uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
