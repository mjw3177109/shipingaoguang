# BasketCut V1.4：长录像稳定性与生产验证

## 已解决问题

原先 CPU 默认每 0.35 秒采样，最多 10000 帧；对 1–2 小时录像可能只能看到前约 58 分钟。V1.4 把长视频的实际采样间隔调整为 max(用户设置间隔, 视频时长/最大帧数)，从而在不超过总采样预算的前提下覆盖全部时间轴。

分析 JSON 新字段：requested_sample_interval_seconds、sample_interval_seconds、adaptive_sampling、coverage_ratio、incomplete_scan、analyzed_until_seconds。

**技术时间轴覆盖率 100% 不等于实际进球/投篮 Recall 100%。** 采样变疏会漏掉短事件，所有 AI 候选仍必须人工审核。视频解码提前中断时，记录不完整扫描，V1.4 压力验收直接失败。

## CI 默认测试（不使用收费 GPU）

工作流：.github/workflows/v14-long-stress.yml

1. 下载 V1.3 原已使用的 CC0 篮球短视频 Basketpass.webm。
2. 低画质 320px/2fps 反复播放成 3600 秒工程压力样本，保留原视频作者和许可标签。
3. 测试文件流式写入 Django FileField 和 V1.2 用户视频配额统计；不用一次把整个长视频读进内存。
4. 真正运行 CPU Worker 全时段扫描，校验至少 99.9% 时间轴覆盖。
5. 通过真实 Web 排队接口两次提交同一审核快照，验证去重；真实 Worker 调用 FFmpeg 导出 H.264/AAC MP4。
6. 模拟一个过期的 Worker 租约，拒绝旧令牌写入，再真正重跑该导出；不把模拟租约过期说成实际 SIGKILL 恢复。
7. 采样 Linux Worker 进程及其直接子进程的 RSS、媒体卷和临时目录空闲空间变化、各阶段耗时。
8. 运行严格验收脚本，输出每项 PASS/FAIL 的 JSON 报告。上传报告和短视频，不上传长源片。

对应文件：
- scripts/v14_make_long_fixture.py：生成**循环**测试录像
- scripts/v14_long_stress.py：真实 CPU/FFmpeg 工程测试
- scripts/v14_acceptance.py：逐条机器检查
- highlights/test_v14_long.py：长时间轴、故障中断、流式写入测试

可审计产物：v14_fixture_manifest.json、v14_stress_report.json、v14_acceptance.json、highlight.mp4。

CI 门槛：整个压测不超过 1800 秒，观测到的 Python Worker 加直接子进程 RSS 不超过 2048MiB。采样可能错过瞬时尖峰，不构成生产机器容量保证。

## 使用完整真实比赛自行验证

使用独立测试机，设置以下环境变量，严禁在生产数据库直接运行：

    export DEBUG=1
    export DATABASE_URL=sqlite:////tmp/basketcut-v14-private/db.sqlite3
    export MEDIA_ROOT=/tmp/basketcut-v14-private/media
    export BASKETCUT_V14_ISOLATED=YES
    mkdir -p /tmp/basketcut-v14-private/media /tmp/basketcut-v14-private/out
    python manage.py migrate --noinput
    python scripts/v14_long_stress.py \
      --video "/path/to/authorized-full-game.mp4" \
      --out "/tmp/basketcut-v14-private/out" \
      --kind real \
      --source-url "private-operator-provided-recording" \
      --license "rights-confirmed-by-operator" \
      --confirm-isolated
    python scripts/v14_acceptance.py \
      --out /tmp/basketcut-v14-private/out \
      --max-elapsed-seconds 3600 --max-rss-mib 3072

脚本必须要求确认独立 SQLite、DEBUG 和显式 BASKETCUT_V14_ISOLATED 标志。测试时会创建、删除测试数据；不会删除个人真实源视频。

真实源必须是 60–120 分钟连续比赛视频，需独立核实录制许可、版权、人物肖像和个人信息保护。测试者输入 --kind real 仅代表其声明，并不等于程序独立验证真实连续性、许可及素材权利。报告仍标记 real_full_game_independently_verified=false。

## 不能用 V1.4 CI 得出的结论

- 60 分钟样本来自**真实篮球短视频重复**，不是 60 分钟完整连续比赛。
- 测试使用 Django FileField 流式写入，不是浏览器/反向代理完整 HTTP 大文件上传；网关 /tmp 与断流仍待测试。
- 过期租约故障是模拟状态变更，并非真实 Worker 进程突然死亡。
- 磁盘数值是空闲量观测差值，并非物理空间预留或多租户并发容错。
- 模拟审核并非真人专家审核；不测篮球命中率、投篮 Precision/Recall/F1。

后续生产门槛：至少三场自有/明确授权完整比赛、真公网大文件上传、SIGKILL 恢复、PostgreSQL 并发配额和独立事件标签效果统计。
