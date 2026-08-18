FROM python:3.11.9-alpine

# 设置时区为上海 (定时运行 schedule_time 按此时间计算)
RUN apk add --no-cache tzdata && \
    cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

# 设置代理环境变量
# ENV HTTP_PROXY=http://192.168.111.14:7893
# ENV HTTPS_PROXY=http://192.168.111.14:7893

# 使用国内 pip 镜像源（阿里云）
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

# 安装依赖 (含 tag_down/reply_down 所需的 XClientTransaction、beautifulsoup4)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app

# Copy app source code
COPY *.py /app/
COPY settings.json /app/

# 容器启动时自动运行 main.py (定时任务在程序内循环)
CMD ["python", "main.py"]