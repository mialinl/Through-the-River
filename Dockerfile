FROM python:3.12-slim

WORKDIR /app

# 国内网络拉 PyPI 慢或断的话：docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=""
COPY requirements.txt ./
RUN pip install --no-cache-dir ${PIP_INDEX_URL:+-i "$PIP_INDEX_URL"} -r requirements.txt

COPY river/ ./river/

ENV RIVER_DATA=/data \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8000

CMD ["python", "-m", "river", "serve", "--host", "0.0.0.0", "--port", "8000"]
