# 使用官方 Python 镜像作为基础
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖，包括 sudo 和 curl
RUN apt-get update && apt-get install -y sudo curl && rm -rf /var/lib/apt/lists/*

# 安装napcat
RUN curl -o napcat.sh https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh && bash napcat.sh --docker n --cli y

# 先只复制依赖文件
COPY requirements.txt .

# 只有当 requirements.txt 变化时，这一层才会重新构建
RUN pip install -r requirements.txt --no-cache-dir

# 将src目录下的所有文件复制到容器的/app目录中
COPY src/ /app/

# 容器启动时运行的命令
CMD ["python", "main.py"]