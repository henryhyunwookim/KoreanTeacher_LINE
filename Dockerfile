FROM python:3.11-slim

WORKDIR /app

# Ensure we do not run as root
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

# Install ffmpeg for audio conversion
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# Run as non-root
USER appuser

# Expose port (Cloud Run sets the PORT env variable)
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
