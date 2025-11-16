FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt /app/requirements.txt

# Install Python packages from requirements
RUN pip install --no-cache-dir -r requirements.txt

# Create logs directory
RUN mkdir -p /app/logs /app/certs

# Copy application files
COPY logger.py /app/logger.py
COPY mongo_client.py /app/mongo_client.py

# Expose proxy port
EXPOSE 8080

# Run mitmdump with the logger
CMD ["mitmdump", "-s", "/app/logger.py", "--listen-port", "8080", "--ssl-insecure", "--set", "confdir=/app/certs"]
