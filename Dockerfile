FROM python:3.13.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1

WORKDIR /app
COPY requirements.txt ./
COPY backend/requirements-analyzer.txt backend/requirements-models.txt ./backend/
RUN python -m pip install -r requirements.txt -r backend/requirements-analyzer.txt

# Integrated image requires the exact CUDA wheel family tested by Back 1.
# Weights remain external read-only mounts; .dockerignore excludes private data.
RUN python -m pip install torch==2.10.0+cu128 torchvision==0.25.0+cu128 \
        --index-url https://download.pytorch.org/whl/cu128 && \
      python -m pip install -r backend/requirements-models.txt && \
      python -c "import torch, torchvision; assert torch.version.cuda == '12.8'" && \
      python -m pip check
COPY . .

EXPOSE 8000
CMD ["uvicorn", "backend.api.app:app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
