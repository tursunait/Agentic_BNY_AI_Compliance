FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_USE_DEPRECATED=legacy-resolver

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip wheel --no-cache-dir -r requirements.txt -w /wheels \
    && pip install --no-cache-dir --no-index --find-links /wheels -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
