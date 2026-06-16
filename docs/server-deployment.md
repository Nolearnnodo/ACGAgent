# 服务器登录与生产部署手册

本文记录 ACGAgent 当前生产服务器的连接、部署、验证和故障排查方法。最后核对日期：2026-06-10。

## 1. 安全约束

以下内容不得写入本文、README、提交记录、Issue 或聊天截图：

- SSH 私钥、私钥文件内容和登录密码
- `/opt/acgagent/.env` 的实际内容
- `LLM_API_KEY`、`NEO4J_PASSWORD`、`JWT_SECRET_KEY`
- 管理员账号密码、访问令牌和刷新令牌
- SQLite、Neo4j 的完整生产数据备份

敏感信息只保存在服务器 `/opt/acgagent/.env`、本机安全环境或密码管理器中。仓库只保留 `.env.example` 的占位值。

## 2. 服务器信息

| 项目 | 当前值 |
|---|---|
| SSH 主机 | `47.100.65.191` |
| SSH 用户 | `root` |
| 公网入口 | `http://47.100.65.191/` |
| 健康检查 | `http://47.100.65.191/api/v1/health` |
| 服务器部署目录 | `/opt/acgagent` |
| 生产 Compose | `/opt/acgagent/docker-compose.prod.yml` |
| 生产环境变量 | `/opt/acgagent/.env` |
| 宿主 nginx 配置 | `/etc/nginx/conf.d/acgagent.conf` |
| 前端容器监听 | `127.0.0.1:8080` |

服务器使用 SSH 密钥认证。私钥位置和内容不在仓库中记录。

```powershell
ssh root@47.100.65.191
```

检查免交互连接：

```powershell
ssh -o BatchMode=yes -o ConnectTimeout=10 root@47.100.65.191 "hostname"
```

## 3. 生产架构

生产环境由 [docker-compose.prod.yml](../docker-compose.prod.yml) 管理：

| 服务 | 容器 | 镜像 | 网络暴露 |
|---|---|---|---|
| 前端 | `acgagent-frontend` | `acgagent-frontend:latest` | `127.0.0.1:8080 -> 80` |
| 后端 | `acgagent-backend` | `acgagent-backend:latest` | 仅 Compose 内部 `8000` |
| Neo4j | `acgagent-neo4j` | `neo4j:5.20-community` | 不暴露公网 |

宿主 nginx 监听公网 `80`，将请求转发到 `127.0.0.1:8080`。前端容器 nginx 再将 `/api/` 转发到后端容器。

持久化卷：

- `acgagent_sqlite_data`
- `acgagent_neo4j_data`
- `acgagent_neo4j_logs`

禁止执行 `docker compose down -v`，该命令会删除 SQLite 和 Neo4j 数据卷。

## 4. 常规部署

本机要求：

- Windows PowerShell
- Docker Desktop 正常运行
- `ssh`、`scp` 可用
- SSH 密钥可以登录服务器

服务器访问 Docker Hub 曾出现超时，因此当前推荐在本机构建镜像，再由脚本通过 `scp` 传到服务器。

在仓库根目录执行全量部署：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy-prod.ps1
```

只部署后端：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy-prod.ps1 -Service backend
```

只部署前端：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy-prod.ps1 -Service frontend
```

跳过构建，重新传输本机已有镜像：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy-prod.ps1 -SkipBuild
```

部署脚本会依次执行：

1. 检查 Docker、Compose、SSH 和 SCP。
2. 在本机构建生产镜像。
3. 使用 `docker save` 导出镜像包。
4. 同步 `docker-compose.prod.yml` 和镜像包。
5. 在服务器执行 `docker load`。
6. 使用 `docker compose up -d --no-build` 重建服务。
7. 请求健康检查接口。

前端生产构建必须使用相对 API 地址 `/api/v1`。不要让本机 `.env` 中的 `http://localhost:8000/api/v1` 进入生产包。

## 5. 部署后验证

本机验证公网入口：

```powershell
curl.exe --noproxy "*" -I http://47.100.65.191/
curl.exe --noproxy "*" http://47.100.65.191/api/v1/health
```

健康接口应返回：

```json
{"status":"ok"}
```

服务器查看容器状态：

```bash
cd /opt/acgagent
docker compose -f docker-compose.prod.yml ps
```

查看日志：

```bash
docker logs --tail 200 acgagent-backend
docker logs --tail 200 acgagent-frontend
docker logs --tail 200 acgagent-neo4j
```

持续跟踪后端日志：

```bash
docker logs -f acgagent-backend
```

后端容器启动时会先执行：

```bash
alembic upgrade head
```

因此新增 Alembic 迁移后，重新部署后端即可执行迁移。

## 6. nginx 运维

当前 ACGAgent 配置：

```text
/etc/nginx/conf.d/acgagent.conf
```

修改后必须先检查语法，再重载：

```bash
nginx -t
nginx -s reload
```

当前服务器还保留：

- `/etc/nginx/conf.d/tjti.conf.disabled`：已停用的 TJTI 配置
- `/etc/nginx/conf.d/snaptally.conf`：保留的其他域名配置
- `/etc/nginx/conf.d/snaptally.conf.bak-acgagent`：切换默认站点时的备份

不要删除或覆盖 `snaptally.conf`。ACGAgent 只负责 IP/default 请求，`snaptally.cc` 仍应匹配原有域名配置。

## 7. 手动部署备用流程

部署脚本不可用时，可按以下流程手动更新。

本机构建并导出镜像：

```powershell
docker compose -f docker-compose.prod.yml build backend frontend
docker save acgagent-backend:latest acgagent-frontend:latest -o $env:TEMP\acgagent-images.tar
scp .\docker-compose.prod.yml root@47.100.65.191:/opt/acgagent/docker-compose.prod.yml
scp $env:TEMP\acgagent-images.tar root@47.100.65.191:/tmp/acgagent-images.tar
```

服务器加载并更新：

```bash
docker load -i /tmp/acgagent-images.tar
cd /opt/acgagent
docker compose -f docker-compose.prod.yml --env-file .env up -d --no-build
docker compose -f docker-compose.prod.yml ps
```

部署完成后可删除临时镜像包：

```bash
rm -f /tmp/acgagent-images*.tar
```

## 8. 更新前备份镜像

较大改动前，可先给当前镜像增加备份标签：

```bash
docker tag acgagent-backend:latest acgagent-backend:backup-YYYYMMDDHHMM
docker tag acgagent-frontend:latest acgagent-frontend:backup-YYYYMMDDHHMM
```

如需回退：

```bash
docker tag acgagent-backend:backup-YYYYMMDDHHMM acgagent-backend:latest
docker tag acgagent-frontend:backup-YYYYMMDDHHMM acgagent-frontend:latest
cd /opt/acgagent
docker compose -f docker-compose.prod.yml up -d --no-build --force-recreate backend frontend
```

数据库结构已经迁移后，镜像回退不代表数据库迁移自动回退。涉及 Alembic 降级时必须单独评估。

## 9. 常见故障

### 9.1 服务器构建时 Docker Hub 超时

不要反复在服务器执行 `docker compose build`。使用本机部署脚本，将本机镜像导出后传入服务器。

### 9.2 前端请求 `localhost:8000`

检查生产包：

```bash
docker exec acgagent-frontend sh -c \
  "grep -R 'localhost:8000' -n /usr/share/nginx/html/assets || true"
```

正常生产包不应出现 `localhost:8000`，API 基址应为 `/api/v1`。确认 [docker-compose.prod.yml](../docker-compose.prod.yml) 中的构建参数没有被改回环境变量覆盖。

### 9.3 前端页面正常但 API 失败

依次检查：

```bash
curl http://127.0.0.1:8080/api/v1/health
curl http://127.0.0.1/api/v1/health
docker logs --tail 200 acgagent-backend
```

### 9.4 注册或登录失败

查看后端状态码：

```bash
docker logs --tail 200 acgagent-backend | grep "/api/v1/auth/"
```

常见状态：

- `400`：邮箱已注册等业务错误
- `401`：邮箱或密码错误
- `422`：邮箱格式、密码长度或请求体校验失败

### 9.5 Neo4j 未就绪

```bash
docker inspect --format "{{json .State.Health}}" acgagent-neo4j
docker logs --tail 200 acgagent-neo4j
```

后端依赖 Neo4j 健康检查，Neo4j 未进入 `healthy` 时后端不会正常启动。

## 10. 敏感信息维护

需要修改密钥时，登录服务器后编辑：

```bash
cd /opt/acgagent
chmod 600 .env
```

使用服务器上的文本编辑器修改 `.env`，不要将文件下载到仓库，也不要在命令历史中直接写入密钥值。

修改后按影响范围重启：

```bash
docker compose -f docker-compose.prod.yml --env-file .env up -d --no-build backend
```

管理员账号由密码管理器维护。本文不记录管理员邮箱、密码或令牌。
