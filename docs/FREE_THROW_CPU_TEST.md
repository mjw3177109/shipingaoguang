# CPU 实战对比：传球（负对照） vs 罚球（投篮片段）

- 已有传球样例：[Basketpass.webm](https://commons.wikimedia.org/wiki/File:Basketpass.webm)，作者 Benoît Prieur，CC0 1.0，5.683 秒。
- 新增明确罚球描述样例：[VCB FCB(4).webm](https://commons.wikimedia.org/wiki/File:VCB_FCB(4).webm)，作者 Coentor，CC BY-SA 3.0，约 5.8 秒。
- 完全免费 CPU / OpenCV，不调用 GPU；每次 GitHub Actions 从源站直接下载，不提交原片到 GitHub。
- 真实跑 `highlights.vision.cpu` 分析两段，并生成每段 `diagnostics.json`、`cpu_proposals.json`、`contact_sheet.jpg`，可查看画面与候选时间段。
- 罚球片段的弱标签来自公开说明「Tir lliure」；并**没有逐帧人工标注出手秒数**，所以不产生 Precision / Recall 等检测准确率数值。
- 罚球视频的分享衍生预览遵循 CC BY-SA 3.0 和署名要求。
- GitHub Action 可以手动重跑，测试链接见 Actions > Real Free Throw CPU Comparison。

> 模型边界：当前 CPU OpenCV 是颜色+轮廓+时间筛选的基线，标签是 **ball activity** 而不是 made basket。下一步用视频逐帧人工定位“出手时间”，再通过 `highlights.vision.evaluate` 算真正候选召回率，避免把传球误认为投篮。
