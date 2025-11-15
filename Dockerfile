FROM python:3.11-slim

# Install mitmproxy and dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
RUN pip install --no-cache-dir \
    mitmproxy==10.1.6 \
    pymongo==4.6.1

# Create app directory
WORKDIR /app

# Create logs directory
RUN mkdir -p /app/logs /app/certs

# Copy logger script
COPY logger.py /app/logger.py

# Expose proxy port
EXPOSE 8080

# Run mitmdump with the logger
CMD ["mitmdump", "-s", "/app/logger.py", "--listen-port", "8080", "--ssl-insecure", "--set", "confdir=/app/certs"]
