# 免费 CPU + 真实篮球视频验收（非进球准确率测试）

## 视频及授权
- Source: [Basketpass.webm](https://commons.wikimedia.org/wiki/File:Basketpass.webm)
- Creator: Benoît Prieur; Wikimedia Commons derivative by Mariusz Swornóg
- Licensing: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/)
- Runtime: about 5.7s, 1280×720 VP8 WebM; actual basketball match/ball passing footage.
- Repo **does not commit the original media**. GitHub Actions downloads it on demand.
- This is **a passing clip**, not verified made basket. It tests video processing and honest zero-candidate fallback, **not shot recall**.

## How the automated real-footage test works
1. GitHub Actions downloads CC0 real basketball video, validates bytes and FFprobe metadata.
2. Django migration; test user and court created in temporary CI workspace.
3. POST upload via actual Django view and private storage.
4. POST `engine=cpu` queue, run actual CPU analysis worker (OpenCV HSV circular contour and movement heuristic).
5. If a candidate exists, simulated test reviewer approves one. If none exist, add explicit manually selected candidate, avoiding dishonest false claims.
6. POST export task and invoke the real FFmpeg worker.
7. Verify readable output MP4, nonzero bytes, duration; write report and JPEG preview.
8. GitHub Actions artifact includes `highlight.mp4`, `preview.jpg`, `report.json`.

The report explicitly distinguishes `cpu_candidate_count` and `manual_fallback`.
Successful workflow execution means **engineering integration works**, not that the model accurately detects shots.

## Run
In GitHub Actions choose **Real Basketball CPU Smoke (CC0)** → Run workflow.
If network download is blocked, GitHub will report failure rather than silently use synthetic footage.
Free Actions minutes are subject to your GitHub plan quotas; this is NOT guaranteed free unlimited cloud CPU.

To run locally:
```bash
python -m pip install -r requirements.txt -r requirements-cpu.txt
python manage.py migrate
python scripts/real_video_smoke.py --video Basketpass.webm --out real-video-results
```

For meaningful shot recall, annotate independent longer match clips from authorized data and evaluate with `highlights.vision.evaluate`. It would be incorrect to calculate shot detection accuracy from a 5.7-second passing example alone.

## 2026-10-19 实际运行结果（不是合成数据）

证据：[GitHub Actions Run #39215098572](https://github.com/mjw3177109/shipingaoguang/actions/runs/39215098572)。
报告与真实 MP4 产物：[Artifact #12169012111](https://github.com/mjw3177109/shipingaoguang/actions/runs/39215098572#artifacts)（GitHub 保留 7 天）。

| 指标 | 实测值 |
|---|---:|
| 原片大小 | 1,663,690 字节 |
| 原片时长 | 5.683 秒 |
| CPU 自动候选数量 | 1 段 |
| 候选时间窗口 | 1.20–5.68 秒 |
| 候选启发式分数 | 0.8276（非进球概率） |
| 是否需要手动补片 | 否 |
| 输出时长 | 4.467 秒 |
| 输出大小 | 1,465,093 字节 |
| 端到端脚本用时 | 2.77 秒 |
| 导出结果 | FFmpeg MP4，已成功 |
| 原片 SHA256 | `a5d909156fef2abcf08428aaea2f15a234d9d7ac9aadd81f57a01e2b7dc2b1eb` |
| 输出 SHA256 | `a6a7b3b5be5711cc10d1590cc7daa2fccd60125592c99ee3281aa514bf71fe63` |

**审核说明**：上述“审核通过”由自动集成测试的模拟审核操作完成，未经过真人逐帧确认。真实比赛片段是篮球**传球**，未提供逐次投篮尝试的独立真值。故无法计算真实 Recall/Precision，更不能宣传其为“AI 自动识别进球成功”。

第二项检测依然需要**投篮真实比赛录像**（最好是固定机位 30–60 秒、有明确出手的片段），在 `benchmarks` 工作流里做人工标注和检测效果评估。
