# 前端镜像（开发模式）：node + vite dev server，启用 HMR 热更新。
# docker-compose 把宿主机 ./frontend 挂进 /app/frontend，改代码立即生效。
FROM node:20-slim

WORKDIR /app/frontend

# 仅装依赖：源码靠挂载提供，避免每次改源码都重 build
COPY frontend/package.json /app/frontend/
COPY frontend/package-lock.json* /app/frontend/

RUN npm install

EXPOSE 5173

CMD ["sh", "-c", "npm install && npx vite --host 0.0.0.0 --port 5173"]
