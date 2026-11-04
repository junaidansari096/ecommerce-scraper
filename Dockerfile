FROM python:3.11-slim

# Install system dependencies & Chromium for headless stealth scraper
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Ensure storage directories exist
RUN mkdir -p user_data chrome_scraper_profile data

# Google Cloud Run default port is 8080
ENV PORT=8080
EXPOSE 8080

# Use shell exec form so $PORT expands dynamically in Cloud Run
CMD exec python -m uvicorn server:app --host 0.0.0.0 --port ${PORT}
