# 生产部署说明（可选）

## Uvicorn（开发 / 小规模）

默认镜像命令：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Gunicorn + Uvicorn Worker（多进程）

在宿主机或镜像内额外安装：

```bash
pip install gunicorn uvicorn[standard]
```

示例（4 个工作进程，按需调整）：

```bash
gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000 --timeout 120
```

**说明**：

- 多进程时，内存中的「分析缓存」**不共享**；如需分布式缓存请改为 Redis 等（见 [`todo.md`](../todo.md)）。
- `timeout` 应大于单次大模型调用的可能耗时（参考 `REQUEST_TIMEOUT_SECONDS`）。

## 反向代理

建议在应用前放置 Nginx / Caddy / Traefik，处理 TLS、压缩与更细粒度限流。

## 环境变量注入

生产环境请使用密钥管理（K8s Secret、云厂商参数存储等），与 [`CONFIG.md`](CONFIG.md) 中的清单一致。
