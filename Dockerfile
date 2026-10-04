FROM python:3.12-slim

WORKDIR /code

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# --proxy-headers: Render terminates TLS and forwards the real scheme/host, so
# uvicorn has to trust those headers or generated URLs come back as http://.
# --forwarded-allow-ips=* is safe here only because the container is reachable
# exclusively from Render's private network, never from the internet directly.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]