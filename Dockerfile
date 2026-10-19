FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./
RUN mkdir -p /app/media /app/staticfiles
EXPOSE 8000
CMD ["gunicorn","basketcut.wsgi:application","--bind","0.0.0.0:8000","--workers","2","--timeout","180"]
