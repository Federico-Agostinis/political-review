FROM python:3.11-slim

WORKDIR /workspace

# Install system dependencies if needed (e.g. git)
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY config/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy everything
COPY . .

# Default command (can be overridden)
CMD ["python", "scripts/docs_scraper.py"]
