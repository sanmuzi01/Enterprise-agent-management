# 后端镜像（api / worker 共用，靠 command 区分）。
FROM python:3.12-slim

# FORWARDED_ALLOW_IPS：只有从这些地址（本机、Docker 内网）连进来的请求，才采信 X-Forwarded-For。
# 客户端 IP 取"从右往左第一个不在此列表里的地址"：nginx 追加的真实连接 IP 在最右边，
# 用户自己伪造的在左边取不到。不要改成 *（那样会取最左边，用户能伪造 IP 绕过登录 / 短信限流）。
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FORWARDED_ALLOW_IPS="127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"

WORKDIR /app

# pdf/docx 解析、lxml、chromadb 的编译依赖
# APT_MIRROR：访问 Debian 官方源不稳（国内常见 502）时，构建参数里指定镜像站，例如 mirrors.aliyun.com；默认不改。
# Acquire::Retries：临时的 502 / 超时自动重试几次。
ARG APT_MIRROR=
RUN if [ -n "$APT_MIRROR" ]; then \
        for f in /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list; do \
            if [ -f "$f" ]; then sed -i "s#deb.debian.org#${APT_MIRROR}#g; s#security.debian.org#${APT_MIRROR}#g" "$f"; fi; \
        done; \
    fi \
    && apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
        build-essential libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

# 先装 CPU 版 torch，避免 sentence-transformers 拖进几个 G 的 CUDA 轮子
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch

COPY requirements.txt .
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --index-url "$PIP_INDEX_URL" -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "FasdtApi.main:app", "--host", "0.0.0.0", "--port", "8000"]
