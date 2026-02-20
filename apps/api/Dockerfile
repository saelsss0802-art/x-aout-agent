FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
COPY packages/core /app/packages/core
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

EXPOSE 8000

CMD ["sh", "-c", "python -m alembic -c apps/api/alembic.ini upgrade head && python -m uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000"]
