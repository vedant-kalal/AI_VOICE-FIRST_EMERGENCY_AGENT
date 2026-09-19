# Same base as ai-callcenter (Python 3.12 slim). No ffmpeg needed: mu-law clips use stdlib wave+audioop.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Never bake secrets into the image: .env is git/docker-ignored, pass config via environment.
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
