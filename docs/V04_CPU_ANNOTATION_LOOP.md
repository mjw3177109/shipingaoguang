# BasketCut V0.4 — 免费 CPU 标注建议与难负样本闭环

## 为什么做 V0.4

V0.3 在 **4 张真实篮球框**的试验中，独立验证集 TP=0、FP=110、FN=2。工程上能够训练，但实测质量不合格。主要缺口不是“多跑几轮”，而是球与非球（球衣、广告、地板、看台、篮圈）真实数据量过少。

## 新增的真实能力

1. Django 球框标注页的 **CPU 自动挑选待审帧**按钮。
2. 由已有 `runworker` 执行，不占用 Django HTTP 请求；每个视频最多实际取样 96 帧、保留至多 24 个时间点，无 GPU/模型权重。
3. 橙色轮廓疑似篮球的复杂画面、快速变化画面、低运动普通背景分别产生时间点建议，避免全是橙色物体。
4. 所有建议只有 `time_seconds`、`reason`、`score`；**绝不自动填“篮球存在”或“无球”**。用户点建议后，人工查看图像，框住球，或明确点“无球”。
5. 提交人工标注后仅移除对应建议，不改动已有人工真值，不会自动重新确认素材训练授权。
6. 管理命令 `audit_ball_labels --owner-id <ID>` 检查已授权场次的人工正/负标签数量。
7. 训练集导出增加 `quality_report.json` 和 manifest 中的 `quality_audit`；用整体比赛拆 train/val 后提示是否具备基本样本覆盖能力。

### 数据覆盖检查（只是建议门槛）

- 至少 4 场不同比赛，train/val 各至少 2 场；
- 至少 120 个手工篮球框、40 张人工确认无球负样本；
- 训练和验证集合都至少有正样本和负样本。

这些是研发阶段**数量和覆盖检查**，不是“自动进球判断准确率”；通过不代表可以商业部署。下一阶段仍需要完全独立的测试集和逐帧人工评测。

## 操作

```bash
git pull origin main
docker compose up -d --build
```

打开任一已上传比赛 → **篮球位置框标注** → 点「生成 24 个以内待审帧（CPU）」→ 后台处理完成刷新 → 点建议的秒数 → 逐帧人工框选球或勾选无球 → 保存 → 明确确认素材训练权利。

统计与导出（仅可信管理员操作）：

```bash
python manage.py audit_ball_labels --owner-id 1
python manage.py export_ball_dataset --owner-id 1 --out /tmp/approved-ball-labels
cat /tmp/approved-ball-labels/quality_report.json
```

训练权重仍标记 `research_only=true`；不默认替换线上 CPU 颜色候选与声音候选。

## 真实视频 CPU 验证

工作流 `.github/workflows/real-annotation-sampler-cpu.yml` 从 Wikimedia Commons 下载已明确许可的传球与罚球素材，运行真实 OpenCV CPU 抽帧，保存 **metadata only** 的 JSON artifact。该验证是“算法确实在真实视频上找出需要人工查看的时间点”，不是负样本标注正确率或进球识别率。

素材：Basketpass.webm（Benoît Prieur，CC0）；VCB FCB(4).webm（Coentor，CC BY-SA 3.0）。不得将未获授权的第三方视频上传公共仓库，拍摄及个人肖像授权独立审查。

## 后续应避免的错误

- 把所有橙色像素自动当篮球；把没有识别到篮球的画面自动当无球；
- 按相邻帧随机分训练/测试，夸大泛化表现；
- 只有 4 张样本或只测短片就宣称商用；
- 为了“0 误报”把阈值调得过高，最终什么都检测不到；
- 使用 GitHub Actions 免费 CPU 做长期无人值守收费产品推理。它是开发 CI 环境，不是正式生产算力。
