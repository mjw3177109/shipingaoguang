# CPU 实战对比：传球描述样例 vs 罚球描述样例（两者均未完成逐帧真值标注）

- 已有传球样例：[Basketpass.webm](https://commons.wikimedia.org/wiki/File:Basketpass.webm)，作者 Benoît Prieur，CC0 1.0，5.683 秒。
- 新增明确罚球描述样例：[VCB FCB(4).webm](https://commons.wikimedia.org/wiki/File:VCB_FCB(4).webm)，作者 Coentor，CC BY-SA 3.0，约 5.8 秒。
- 完全免费 CPU / OpenCV，不调用 GPU；每次 GitHub Actions 从源站直接下载，不提交原片到 GitHub。
- 真实跑 `highlights.vision.cpu` 分析两段，并生成每段 `diagnostics.json`、`cpu_proposals.json`、`contact_sheet.jpg`，可查看画面与候选时间段。
- 罚球片段的弱标签来自公开说明「Tir lliure」；并**没有逐帧人工标注出手秒数**，所以不产生 Precision / Recall 等检测准确率数值。
- 罚球视频的分享衍生预览遵循 CC BY-SA 3.0 和署名要求。
- GitHub Action 可以手动重跑，测试链接见 Actions > Real Free Throw CPU Comparison。

> 模型边界：当前 CPU OpenCV 是颜色+轮廓+时间筛选的基线，标签是 **ball activity** 而不是 made basket。下一步用视频逐帧人工定位“出手时间”，再通过 `highlights.vision.evaluate` 算真正候选召回率，避免把传球误认为投篮。

## 2026-10-19 第一次真实 CPU 对比

测试成功：[Actions #39216262523](https://github.com/mjw3177109/shipingaoguang/actions/runs/39216262523)

| 公开视频 | FFprobe 实际时长 | CPU 活动候选 | 活动区间 |
|---|---:|---:|---|
| Basketpass.webm | 5.680 秒 | 1 段 | 1.00–5.68 秒 |
| VCB FCB(4).webm | 5.767 秒 | 1 段 | 1.50–5.767 秒 |

**发现**：现有 HSV CPU 基线只确认橙色圆形物体活动，两种片段均产生候选，尚无法区分投篮和传球。这一结果不能作为投篮识别准确率。

新增 `annotated_detections.jpg`（每个识别物体用绿色圆圈标注）与 `frame_observations.json`，帮助排查误检是不是来自橙色球衣、看台或篮球。
