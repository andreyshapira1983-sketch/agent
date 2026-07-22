FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/agent

COPY requirements.lock ./requirements.lock
RUN python -m pip install --upgrade pip \
    && python -m pip install --require-hashes -r requirements.lock

# Keep a complete fallback copy in the image. docker-compose mounts the live
# repository at /workspace so code, memory, logs and approvals survive rebuilds.
COPY . /opt/agent

CMD ["python", "agent_tick.py", "--workspace", "/opt/agent"]
