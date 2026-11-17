# K8s 部署说明

用 K8s 替代「zip 打包 + deploy.sh + docker compose」的部署方式。

## 与 Compose 部署的对应关系

| Compose | K8s |
|---|---|
| `db` 服务 + `db-data` 卷 | `10-postgres.yaml`（StatefulSet + PVC + Service）|
| `hub` 服务 | `20-hub.yaml`（Deployment + Service）|
| `.env` 中的密码/密钥 | Secret（`martin-db`、`martin-hub`）|
| 服务器 8000 端口 / 前置 Nginx | Service + Ingress（`30-ingress.yaml`）|

镜像仍用现有 `Dockerfile` 构建，entrypoint 里的「等 DB → alembic 迁移 → 启动」逻辑在 K8s 下照常工作。

## 步骤

1. 构建并推送镜像：
   ```sh
   docker build -t <registry>/martin-hub:v1 .
   docker push <registry>/martin-hub:v1
   ```
   然后把 `20-hub.yaml` 里的 `image:` 改成该地址。

2. 修改 Secret 里的 `POSTGRES_PASSWORD` 和 `MARTIN_SECRET_KEY`（务必改掉默认值），以及 `30-ingress.yaml` 的域名。

3. 部署：
   ```sh
   kubectl apply -f k8s/
   kubectl -n martin rollout status deploy/martin-hub
   ```

4. 创建管理员（对应 deploy.sh 里的 step 2）：
   ```sh
   kubectl -n martin exec deploy/martin-hub -- \
     martin admin create-admin --username admin --password '<强密码>'
   ```

## 常用命令

```sh
# 日志（替代 docker compose logs -f hub）
kubectl -n martin logs -f deploy/martin-hub

# 升级（替代重新执行 deploy.sh）
kubectl -n martin set image deploy/martin-hub hub=<registry>/martin-hub:v2
kubectl -n martin rollout status deploy/martin-hub

# 备份数据库（替代 docker compose exec db pg_dump）
kubectl -n martin exec martin-db-0 -- \
  sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > martin-$(date +%F).dump
```

## 注意事项

- Hub 副本数为 1 且使用 `Recreate` 策略：迁移在容器启动时执行，避免多副本并发跑 `alembic upgrade head`。要水平扩容需先把迁移移到单独的 initContainer 或 Job。
- 如果用云托管 PostgreSQL，删除 `10-postgres.yaml`，把 `MARTIN_DATABASE_URL` 直接指向托管库。
- 集群没有 Ingress 控制器时，把 hub Service 改为 `NodePort` 直接对外。
