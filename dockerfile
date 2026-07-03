FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl unzip && \
    curl "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o "awscliv2.zip" && \
    unzip awscliv2.zip && \
    ./aws/install && \
    rm -rf awscliv2.zip aws && \
    rm -rf /var/lib/apt/lists/*

COPY setup.py setup.cfg ./
COPY tap_toast/ ./tap_toast/

RUN pip install --no-cache-dir -e .

COPY config.json ./
COPY pc_to_guid_mapping.json ./.secrets/pc_to_guid_mapping.json
COPY output/output.jsonl /app/output/output.jsonl

COPY scripts/run-tap.sh /usr/local/bin/run-tap.sh
RUN chmod +x /usr/local/bin/run-tap.sh

ENTRYPOINT ["run-tap.sh"]