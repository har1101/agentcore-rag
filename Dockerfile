FROM --platform=linux/arm64 ghcr.io/astral-sh/uv:python3.13-trixie

RUN apt-get update && apt-get install -y \
    nodejs \
    npm \
    unzip \
    curl \
    && rm -rf /var/lib/apt/lists/*

# AWS CLI (for s3 sync via InvokeAgentRuntimeCommand)
RUN curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o /tmp/awscli.zip && \
    unzip -q /tmp/awscli.zip -d /tmp && \
    /tmp/aws/install && \
    rm -rf /tmp/aws /tmp/awscli.zip

RUN groupadd -g 1000 appuser && useradd -u 1000 -g 1000 -m appuser

USER appuser

RUN mkdir ~/.npm-global
RUN npm config set prefix '~/.npm-global'

ENV PATH=~/.npm-global/bin:$PATH \
    NODE_PATH=/home/appuser/.npm-global/lib/node_modules

RUN npm install -g @anthropic-ai/claude-code

ENV CLAUDE_CODE_USE_BEDROCK=1 \
    ANTHROPIC_MODEL=sonnet \
    ANTHROPIC_DEFAULT_SONNET_MODEL=global.anthropic.claude-sonnet-4-6 \
    ANTHROPIC_DEFAULT_HAIKU_MODEL=global.anthropic.claude-haiku-4-5-20251001-v1:0

WORKDIR /app
COPY --chown=1000:1000 pyproject.toml uv.lock ./
RUN uv sync --frozen

COPY --chown=1000:1000 . ./

CMD ["uv", "run", "opentelemetry-instrument", "python", "app.py"]
