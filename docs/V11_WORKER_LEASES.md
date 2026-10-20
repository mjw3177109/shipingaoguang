# BasketCut V1.1 · Worker 租约、崩溃恢复与运行状态

## 目标与边界

V1.1 保留 V1.0 的上传、CPU 候选、人工审核、固定剪辑快照、AAC 原声/无声 MP4 导出。重点是防止后台进程被强制关闭后，任务永久停留在“处理中”，并防止过期的旧 Worker 在任务被重新领取后覆盖新结果。

## 本次新增

- **心跳与租约令牌**：比赛分析、CPU 抽帧、MP4 渲染分别写入开始时间、最近心跳、随机 Worker 令牌和自动恢复次数。CPU 逐帧扫描、音频 PCM 提取和 FFmpeg 渲染均会定期续租。
- **过期任务恢复**：默认 900 秒未心跳视为过期。Worker 每次轮询都检查 `analyzing` / `running` 任务；过期任务自动重新排队，默认最多自动恢复 2 次。再次过期会标为失败并显示明确错误，不无限重试。
- **旧 Worker 隔离**：每次领取任务会生成新令牌。旧进程心跳、写进度或提交结果时必须匹配当前令牌；失去租约后会停止 FFmpeg，禁止提交旧结果。旧 Worker 若恰好完成了文件上传，也会删除其孤立输出文件。
- **音频分析内存控制**：FFmpeg 解码出的 PCM 先流入临时文件，再分块计算 RMS，避免将 6 小时视频的全部 PCM 常驻 RAM；过程也会续租。
- **运维状态快照**：新增 `workerstatus` 命令，输出分析、导出、标注抽帧的排队/运行/过期/失败计数，可选 JSON 和非零退出码检查。
- **人工重试**：用户重新排队失败的分析或标注任务时会重置该任务的自动恢复预算；V1.0 导出仍沿用独立的“同一审核快照重试”机制。

## 部署与参数

更新代码后执行：

```bash
docker compose up -d --build
docker compose logs -f worker
```

`.env` 可调整：

```dotenv
WORKER_STALE_AFTER_SECONDS=900
WORKER_MAX_RECOVERIES=2
```

不要把过期时间设得比单个正常处理阶段的预期心跳间隔更短。一般从 900 秒开始；心跳会在视频帧扫描、PCM 分块和 FFmpeg 进程轮询期间持续刷新，因此这不是“视频超过 15 分钟就失败”的时长限制。

查询当前队列：

```bash
docker compose exec worker python manage.py workerstatus
docker compose exec worker python manage.py workerstatus --json
docker compose exec worker python manage.py workerstatus --check-stale
docker compose logs --since=30m worker
```

`--check-stale` 在存在过期的运行中任务时返回非零状态，适合简单监控/告警；它不是公网健康检查，也不保证 Worker 进程一定存活。若只有排队任务而 Worker 容器已经停止，需结合 Docker 容器状态和日志判断。

手动执行一次任务（仅用于维护/排查；它会和现有 Worker 竞争同一个安全队列）：

```bash
docker compose exec worker python manage.py runworker --once
```

## 自动恢复策略

1. Worker 以条件更新领取任务，生成唯一令牌并写入心跳。
2. 长步骤周期性刷新心跳；每次提交候选、进度或最终 MP4 时再检查令牌。
3. 若租约过期，Worker 把任务重新排队并清空旧令牌。旧进程之后不能覆盖新进程的输出。
4. 超过自动恢复次数则将任务标记失败；检查日志、源视频和磁盘后，用户可重新发起分析，导出可按 V1.0 的原审核快照重试。

数据库迁移 `0011_worker_leases.py` 会新增任务租约字段，不删除任何历史录像、人工审核或导出记录。

## 验收范围

CI 应覆盖旧运行任务恢复、过期令牌被拒绝、恢复次数上限、近期心跳不误判、用户重新排队清除旧恢复预算，以及带租约的真实 FFmpeg 导出。V1.1 不等于多机分布式任务队列或商业级高可用；仍建议每个环境保持单个常驻 Worker。租约令牌可防止同一任务被旧进程重复提交，但当前仍建议单 Worker；多 Worker 并行吞吐量、数据库连接池与 CPU 资源配额尚未完成负载验收。
