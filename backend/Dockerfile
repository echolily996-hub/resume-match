FROM python:3.12.10-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock && useradd --create-home appuser
COPY backend/ ./
USER appuser
EXPOSE 8000
CMD ["python", "-m", "app.bootstrap"]
