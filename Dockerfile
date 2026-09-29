# 底子默认是 python:3.12-slim。拉不下来的话，可以换成本机已经有的、带 Python 3.10+ 的镜像，
# 比如 Ombre 的镜像（它本身就基于 python:3.12-slim，还装好了 mcp）：在 .env 里设
#   RIVER_BASE_IMAGE=p0luz/ombre-brain:latest
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

WORKDIR /river

# 国内网络拉 PyPI 慢或断的话：docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=""
COPY requirements.txt ./
RUN pip install --no-cache-dir ${PIP_INDEX_URL:+-i "$PIP_INDEX_URL"} -r requirements.txt

COPY river/ ./river/

ENV RIVER_DATA=/data \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8000

# 换底子时别继承底子镜像自己的启动脚本
ENTRYPOINT []
CMD ["python", "-m", "river", "serve", "--host", "0.0.0.0", "--port", "8000"]
