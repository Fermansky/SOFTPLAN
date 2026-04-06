# syntax=docker/dockerfile:1.6
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY llm-service/requirements.txt ./requirements.txt
COPY pyproject.toml ./pyproject.toml
COPY common ./common
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt && \
    pip install .

COPY llm-service/app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
