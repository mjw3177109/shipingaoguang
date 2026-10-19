# 免费 CPU 预训练视觉模型：对比试验

相比纯 OpenCV 颜色检测，V0.2 增加了可选的 Torchvision COCO `sports ball` 预训练目标检测实验，模型实际运行在 **CPU**，不使用 Kaggle GPU。

- 代码：`highlights/vision/cpu_pretrained.py`
- 实验：`.github/workflows/cpu-pretrained-benchmark.yml`
- 模型：Torchvision Faster R-CNN MobileNetV3-320、可选 ResNet50 FPN
- 用途：检测 `sports ball`，生成待审候选 JSON，绝不宣称自动判定进球。
- 成本：无 GPU 费用，但是 GitHub Actions CPU 分钟及模型下载受套餐和带宽限制。
- 许可证：上线前核对模型权重与数据使用权；当前仅研究试验。

手动命令：
```bash
python -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
python -m pip install -r requirements-cpu.txt
python -m highlights.vision.cpu_pretrained --video match.webm --out model_candidates.json --arch mobile320
```

把 `model_candidates.json` 中 `candidates` 导入同一场比赛 Django 工作台，所有片段默认待审。若模型没有检测到球，`candidates` 为 `[]`，不能虚构成功。速度和召回率取决于具体篮球镜头及输入分辨率。

## 免费对比语义

仅比较「是否从视频画面检出运动球」，不是比较「是否识别到罚球/进球」。公开视频的说明标签不是人工逐帧标注，暂无可信的投篮检索 Precision / Recall。

如果小球漏检较多，应首先增加篮筐 ROI 裁剪和篮球专项微调训练，而不是简单提高阈值。

## 首次真实 CPU 预训练模型对比 (2026-10-19)

[GitHub Actions #39217175402](https://github.com/mjw3177109/shipingaoguang/actions/runs/39217175402) 已真实运行，CPU 未发生程序异常。

- 传球片段：MobileNetV3-320 检测到 **0 个** COCO sports-ball 框，0 条候选，分析 2.01 秒；
- 罚球片段：MobileNetV3-320 检测到 **0 个** COCO sports-ball 框，0 条候选，分析 1.45 秒。

这表明在该远景场景下，通用轻量模型的篮球检出效果可能很弱；阈值 .16 时得不到有效检测。后续增加 4 块重叠 ROI 裁剪和 ResNet50 作为**实验**，如果依旧为零，则应停止简单模型堆叠，转向篮球专项权重/标注数据。数字来自真实视频，**不是投篮识别准确率**。
