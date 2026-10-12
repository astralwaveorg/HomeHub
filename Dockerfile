FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOMEHUB_DATA=/data
WORKDIR /app
COPY app.py seed.json ambient.wav ./
COPY static ./static
USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=3)"
CMD ["python", "app.py"]
