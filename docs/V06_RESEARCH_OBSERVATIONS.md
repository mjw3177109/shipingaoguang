# BasketCut V0.6 · CPU 研究球轨迹 → Django 人工审核 → FFmpeg 出片

## 为什么不是直接发布自动进球识别

V0.3 真实四帧训练得出 TP=0 / FP=110 / FN=2，训练权重属于 `research_only`。V0.5 增加按比赛隔离的盲测，但还没有足量已授权的人工框标签。因此**没有经过验证的篮球专项模型**。本阶段只连通研究输出与业务审核/出片，并加强拒绝错误视频和误报的规则。

## 工程变化

- 保留现有 `CPU 视觉候选`、`声音候选`、手动添加、审核及 MP4 渲染入口。
- 新增 `highlights/vision/bridge_v06.py`：导入研究模型的**逐帧观测**，只保留与已标定篮筐相邻、跨至少三个不同时间点且连续位移明显的轨迹。检测框置信度不是投篮成功率；每段默认 `pending`。
- 新增 Django 页面独立导入入口：比赛详情页 → `V0.6 · CPU 研究模型连续球轨迹`，不会复用旧版手动候选审批权限。
- 严格检查当前球馆账号权限、`game_id`、时长、篮筐坐标、视频首尾 64KiB 的轻量身份指纹、模型类型、CPU、全片扫描状态、被截断的观测和有限数字。
- 视频指纹**不是完整文件哈希**（针对普通网站上传的快速对照），不能作为强密码学身份或版权证明。防止误用不同视频；不能替代必要的视频授权确认。
- 无研究候选会显示 0 结果，不会产生自动审核通过、导出或虚假的进球。

## 使用步骤（独立 CPU 研究环境）

1. 在 Django 上传已获授权的比赛视频，标定球筐，下载比赛详情页的篮筐 JSON。
2. 研究环境安装 `torch` + `torchvision` CPU；Django Web 容器不安装 Torch。
3. 使用**与 Django 上传逐字节相同的原视频文件**和自己的研究 checkpoint：

```bash
python -m highlights.vision.cpu_pretrained \
  --video match.mp4 \
  --config game-123-hoops.json \
  --checkpoint /research/basketball-specialist.pt \
  --allow-research-checkpoint \
  --arch mobile320 --tiles 4 --interval 1.2 \
  --max-frames 1800 \
  --out research-observations.json
```

4. 在比赛详情页的 V0.6 导入框上传 `research-observations.json`，系统逐条检查，再保存为待审候选。
5. 人工观看候选片段，调整时间、确认是否值得保留、输入球员标记，按「审核通过」或「剔除」；只有通过的候选才可以进入现有 FFmpeg 输出。

**重要**：V0.6 导入要求视频被**完整扫描**。对完整 40–60 分钟视频，CPU FasterRCNN 可能耗时很久，最多 1800 帧、间隔 1.2 秒也只覆盖约 36 分钟；不符合条件会拒绝导入。请先用短录像进行实际研究，分段长视频与全片记录的功能尚未开发。源文件越长、帧密度越大，观测可能超过 500 条而被标记 `observations_truncated=true` 并拒绝导入。

模型可能在真实远景篮球视频上产生**零有效候选**，不代表程序错误，也不能通过人为伪造“命中率”来绕过验证。V0.5 的 `production_release=false` 继续适用。

## GitHub Actions 实战验证说明

`.github/workflows/v06-real-bridge-smoke.yml` 从 Commons 下载 CC0 的真实篮球传球录像，实际执行 Django 上传、严格研究观测导入、模拟审核和 FFmpeg 导出，存储 mp4、report.json。**为了不谎称当前未通过精度验收的神经网络准确识别到了篮球，CI 里的逐帧研究观测使用明确标为 SYNTHETIC 的测试夹具**。测试只验证“真实视频能经过 V0.6 接口完整出片”，不是模型在真实视频上的检测性能。

真正的检测性能必须按 V0.5 的独立比赛盲测与 V0.2 人工出手时间真值进行测量。没有足够授权视频、球框真值和一致的球场机位前，不对外承诺识别率。
