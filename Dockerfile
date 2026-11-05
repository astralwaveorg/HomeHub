FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4
# ssh 客户端供采集器经专用密钥拉取 Mac mini / OpenWrt 路由器指标；
# NAS 构建机无直连外网时，用 HOMEHUB_BUILD_PROXY 传代理构建参数。
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOMEHUB_DATA=/data
RUN apk add --no-cache openssh-client
WORKDIR /app
COPY app.py catalog.py collector.py icon_library.py seed.json ambient.wav VERSION ./
COPY static ./static
USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=3)"
CMD ["python", "app.py"]
