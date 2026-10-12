FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so code edits do not invalidate this layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .

EXPOSE 2112

# -u: unbuffered output so `docker logs` is live.
ENTRYPOINT ["python", "-u", "main.py"]
