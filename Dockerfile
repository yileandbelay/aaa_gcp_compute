FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY kasina ./kasina
COPY wsgi.py .
RUN useradd -m kasina && mkdir -p /app/instance && chown -R kasina /app/instance
USER kasina
EXPOSE 8000
# Long timeout so large video uploads/streams are not cut off.
# Apply database migrations, then start the app.
CMD ["sh", "-c", "flask --app wsgi db upgrade && exec gunicorn -w 3 --threads 4 -t 300 -b 0.0.0.0:8000 wsgi:app"]
