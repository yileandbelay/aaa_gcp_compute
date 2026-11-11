FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////data/academy.db UPLOAD_DIR=/data/uploads
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY academy ./academy
COPY wsgi.py .
RUN useradd -m academy && mkdir -p /data && chown academy /data
USER academy
# Database and uploads live here; mount a volume to keep them.
VOLUME /data
EXPOSE 8000
# Apply database migrations, then start the app. Long timeout for large video uploads.
CMD ["sh", "-c", "flask --app wsgi db upgrade && exec gunicorn -w 3 --threads 4 -t 300 -b 0.0.0.0:8000 wsgi:app"]
