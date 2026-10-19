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
