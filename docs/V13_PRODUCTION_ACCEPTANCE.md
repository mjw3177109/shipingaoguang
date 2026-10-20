# BasketCut V1.3：真实媒体验收与部署自检

> 增量实现，不覆盖 V1.2；继续免费 CPU 候选、人工审核、真实 FFmpeg 导出。仅有报告、实际运行证据和对应版本的绿色 CI，才能宣布 V1.3 工程验收。

## 1. 部署只读诊断

首先在已有 Django 环境执行迁移（首次初始化或升级时）：

```bash
python manage.py migrate --noinput
python manage.py diagnose --json --strict
```

该诊断验证 Django system check、数据库连通性、未应用迁移、媒体目录可写、媒体盘剩余空间、FFmpeg / FFprobe 可用性、Worker 最近心跳、过期运行任务和资源配置。除短暂创建并删除一个空媒体目录测试文件外，不写入业务数据库、读取用户视频或执行导出。

生产建议先启动 Worker：

```bash
docker compose up -d --build
docker compose exec worker python manage.py workerstatus --json --check-worker
docker compose exec web python manage.py diagnose --json --strict --production
```

`--production` 在普通检查之外校验 DEBUG=false、HTTPS 安全 Cookie、无全开放通配 ALLOWED_HOSTS。生产域名、代理 TLS、备份策略与监控由部署方另行配置和验收，不能由该脚本自动保证。CI 保留 DEBUG=1，**不以 CI 代替生产安全验收**。

`--skip-worker` 仅供尚未启动 Worker 的安装前检；结果将明确记为 SKIP，不得充当完整生产验收。`--strict` 遇任何 FAIL 非零退出；JSON 在退出前仍写出，可留档。

## 2. GitHub Actions 可下载的真实出片证据

新工作流：.github/workflows/v13-production-acceptance.yml。

操作：PR 修改 V1.3 文件时自动触发；也可以进入 Actions → BasketCut V1.3 Production Preflight and Real Media Acceptance → Run workflow。工作流：

1. 安装免费 CPU 依赖与 FFmpeg，创建临时 SQLite DB，验证迁移和新单元测试。
2. 启动一次真正的 Django Worker，运行严格自检；离线心跳不得伪报 PASS。
3. 从 Wikimedia Commons 下载 **Basketpass.webm**（页面标注 CC0 1.0，建议每次使用前复核许可）。
4. 用真实画面 **循环** 制作 75 秒低分辨率测试录像；不是 75 秒连续拍摄的新比赛。
5. 实际测试 Django 上传 → CPU 候选处理 → 模拟审核 → 冻结清单 → 真正 Worker/FFmpeg MP4 出片。
6. 核对片长、SHA-256、H.264/AAC、Worker/任务、配额配置、快照和报告真实性。
7. 归档 JSON 报告、编辑清单、预览与真实测试 MP4，方便审计。

工件：`basketcut-v13-production-preflight-real-media-audit`，保留 7 天。主验收文件为 `acceptance_report.json`，失败报告里的单项结果不能掩盖总体失败。

## 3. 验收范围和禁止的结论

| 事项 | V1.3 可以检验 | 本阶段不能宣称 |
|---|---|---|
| 媒体测试 | 真实公开视频、重复 75 秒，完整 Web/CPU/FFmpeg 流水线 | 未剪辑长达 75 秒的比赛现场 |
| 输出 | 导出 MP4 可播放、片长、音视频编码及 SHA | 自动挑出的全部都是进球 |
| Worker | 心跳、卡死租约/滞留任务概况 | 7×24 小时容灾、高可用 |
| 磁盘/配额 | 本机当前阈值和已有 V1.2 保护回归 | 并发物理空间预留、容器/tmp/网关全链路防爆盘 |
| 负载 | 75 秒压力烟测 | 1–6 小时真实比赛吞吐、并发用户性能或公网体验 |

模拟审核与手动兜底只是端到端功能测试，**不是人类专家打标签，也不是模型准确率评测**。只有独立人工标注的完整比赛集，才能进一步计算 Precision/Recall/F1。

## 4. 下一关：真实长录像和生产级资源验收

使用得到授权的完整 60–120 分钟固定机位比赛录像，优先在独立、已备份的测试环境跑。建议记录上传耗时、CPU 扫描耗时、峰值内存/RSS、原片与临时目录磁盘峰值、候选数、人工复核耗时、导出耗时与失败恢复；至少试验无音频、断流、磁盘余量不足、Worker 中断和重复请求。不要把客户录像放进公开 GitHub 或 CI 工件。
