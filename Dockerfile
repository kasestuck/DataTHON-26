FROM python:3.13.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install -r requirements.txt
COPY . .

# The manifests arrive with Back 1. Build DEV2 alone with the API stub;
# after integration, install the exact CUDA wheel family Back 1 tested.
RUN if [ -f backend/analyzer/__init__.py ] || \
       [ -f backend/requirements-analyzer.txt ] || [ -f backend/requirements-models.txt ]; then \
      test -f backend/requirements-analyzer.txt && \
      test -f backend/requirements-models.txt && \
      python -m pip install -r backend/requirements-analyzer.txt && \
      python -m pip install torch==2.10.0+cu128 torchvision==0.25.0+cu128 \
        --index-url https://download.pytorch.org/whl/cu128 && \
      python -m pip install -r backend/requirements-models.txt && \
      python -c "import torch, torchvision; assert torch.version.cuda == '12.8'" && \
      python -m pip check; \
    fi

EXPOSE 8000
CMD ["uvicorn", "backend.api.app:app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
