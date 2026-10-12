FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-tester.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-tester.txt

COPY . .
RUN DJANGO_DEBUG=1 SPRAWDZARKA_DANE=/tmp/dane python manage.py collectstatic --noinput \
    && useradd --uid 1000 --create-home tester

CMD ["sh", "docker/start.sh"]
