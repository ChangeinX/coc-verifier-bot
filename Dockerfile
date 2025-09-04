# Build stage - install dependencies
FROM python:3.12-slim AS dependencies
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Runtime stage - copy app code
FROM python:3.12-slim AS runtime
WORKDIR /app
COPY --from=dependencies /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=dependencies /usr/local/bin /usr/local/bin
COPY bot.py .
CMD ["python", "bot.py"]
