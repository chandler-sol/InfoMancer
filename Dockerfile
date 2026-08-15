FROM python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 1000 infomancer \
    && useradd --uid 1000 --gid infomancer --create-home --shell /bin/false infomancer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=infomancer:infomancer app app
RUN mkdir -p /app/data && chown -R infomancer:infomancer /app/data
USER infomancer
EXPOSE 8787
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787", "--no-proxy-headers"]
