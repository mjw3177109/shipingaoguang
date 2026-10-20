# BasketCut V1.2：媒体资源保护与服务健康

## 新增能力

1. 上传原视频之前，先校验账号跨球馆的原始录像文件总量、上传大小和媒体盘空闲空间。PostgreSQL 使用用户行锁减少并发上传超额问题。
2. FFmpeg 任务执行之前，根据审核剪辑清单的时长估算临时片段、输出视频及音频回退所需磁盘；不足时拒绝执行，保留现有数据。
3. 每账号默认最多 4 个活跃导出任务（queued/running）；已有 MP4 仍能下载。
4. 三种无个人数据的 GET 探针：/health/live 检查进程、/health/ready 检查数据库和媒体盘、/health/worker 检查后台 Worker 最近心跳。204 为通过，503 为不可用。
5. Worker 即使空闲也会上报心跳，长时间处理视频时也会持续上报；workerstatus --check-worker 可用于告警。
6. Docker Compose Web 与 Worker 容器加入健康检查，Worker 等待 Web 完成迁移后再启动。

## .env 配置

- MEDIA_MIN_FREE_BYTES=1073741824（媒体盘保留 1GiB）
- OWNER_MAX_RAW_BYTES=21474836480（每账户原始视频总量 20GiB）
- OWNER_MAX_ACTIVE_EXPORTS=4（每账户活跃导出）
- EXPORT_TEMP_BYTES_PER_SECOND=3145728（每秒已审片段估算临时输出空间）
- WORKER_HEALTH_MAX_AGE_SECONDS=120（心跳超时阈值）

## 升级命令

1. git checkout main
2. git pull origin main
3. docker compose up -d --build
4. docker compose ps
5. docker compose exec worker python manage.py workerstatus --json --check-worker
6. docker compose logs -f worker

健康探针使用 GET 而不是 HEAD。示例：curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health/ready

## 边界与必须注意的事项

- 原始视频额度是 FileField 已存原片大小的统计，未计入输出 MP4、数据库、临时目录或系统其它应用占用。其它硬件/容器磁盘资源需独立监控。
- 磁盘预检是写入前检查，不是物理空间预留，不能排除检查之后被其它并发任务占用。输出文件大小受实际码率影响。
- Django 上传到视图前可能已经将大文件写入系统临时目录。生产代理必须设置请求体大小和临时目录空间限制，否则上传仍可能填满 /tmp。
- 在 SQLite 环境，select_for_update 不具备 PostgreSQL 同等并发串行保证。正式多用户环境用 PostgreSQL、独立持久媒体盘、备份、HTTPS、访问日志。
- Worker 心跳是工作进程可用性的近似信号，并不等于任务一定成功或服务器资源完全正常。
- 已有人工审核、固定剪辑快照、AAC 音轨、失败恢复保持不变。模型仍非自动进球识别。

## 回归验收

测试覆盖：无数据库、磁盘低余量、无心跳和过期心跳，账号上传额度、账号活跃队列、渲染空间不足时的失败保护、实际 FFmpeg 与旧版流程。
