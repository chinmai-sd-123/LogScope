# Single image used for both the server and the agents (different commands).
FROM python:3.13-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md LICENSE ./
COPY logscope ./logscope
RUN pip install --no-cache-dir -e .

# `logscope <subcommand>` by default (compose overrides per service).
ENTRYPOINT ["logscope"]
CMD ["--help"]
