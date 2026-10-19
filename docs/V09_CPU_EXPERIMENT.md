# BasketCut V0.9 — 免费 CPU 篮球专项训练实验流水线

## 阶段定位

当前已有 Django 球馆录像、CPU 候选、人工球框/无球标注与二次复核、数据授权、按比赛隔离 train/val/test 的训练导出，以及研究模型推理和人工高光视频剪辑。V0.9 是**把训练、统一预测、封存测试集比较和报告连接起来**，不是凭 4 张真实框宣称识别率改善。

本阶段全部代码提交到 GitHub；由于仍无足量经过二次复核和训练用途授权的真实多比赛数据，正式对比**保持 BLOCKED**。此前 V0.3 两场真实短片四张框的模型训练实验已证明不合格：独立验证 TP=0、FP=110、FN=2。V0.9 不会通过无限合成数据抬高准确率。

## 1. 训练数据准备

从已上传的篮球比赛进入 `篮球位置框标注`，利用 V0.8 分轮 CPU 抽样持续积累**人工**正例、明确无球负例，并二次复核。最后在每场比赛确认素材允许用于模型训练。

最低工程条件：

- 至少 8 场不同比赛（全部有明确训练权利依据）；
- 至少 120 个球框正样本和 40 个明确人工无球样本；
- 按完整比赛划分训练/验证/测试（各至少 2 场），封存测试集至少 30 个球框正例、20 个无球负例；
- 全部样本经二次复核，具有复核人和复核时间；
- 验证每张 JPEG 的 SHA256、YOLO bbox 范围、样本归属及人工统计；跨集合相同文件哈希直接阻断。

以上只是研究级**数据入场门槛**，不代表样本多样性已经足够，也不保证训练出可用模型，更不证明训练授权本身合法有效。

在可信 Django 服务器运行：

```bash
python manage.py export_ball_dataset \
  --owner-id 1 --out /data/basketcut-v09-dataset \
  --three-way --verified-only
```

数据集 JPEG、标签和 manifest 仅保留在私有研究环境，不要推入公开 GitHub。使用者需确认球馆视频中的人员隐私、录制及模型训练许可。

## 2. CPU 预检（默认，不下载权重，也不训练）

只需要 Python 标准库；代码不会调用 Torch 或模型权重：

```bash
python -m highlights.vision.experiment_v09 \
  --dataset /data/basketcut-v09-dataset \
  --out /data/basketcut-v09-run
```

输出 `preflight.json` 和 `experiment.json`。若未通过，`stage=BLOCKED` 会列出原因，不会创建 checkpoint。运行目录必须为空且不得在数据集目录内部，以免覆盖旧实验。CLI 不会将**合成/未复核**的训练数据自动转为合格数据。

## 3. 真正执行 CPU 研究训练与旧版对照

请先安装可选的 CPU Torch/Torchvision (可能需要联网下载权重，CPU 可能很慢)：

```bash
python -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
python -m pip install pillow
```

已满足所有真实数据条件后，换一个新的空输出目录，明确使用 `--execute`：

```bash
python -m highlights.vision.experiment_v09 \
  --dataset /data/basketcut-v09-dataset \
  --out /data/basketcut-v09-actual-training \
  --execute --epochs 2 --max-steps 100
```

这是实际可执行的串行步骤：

1. 真实数据 preflight → 阻断缺标签、未复核、造假格式、错误 split/哈希。
2. Torchvision 预训练 MobileNetV3 Faster R-CNN 转成背景/篮球检测头，用 CPU 在 **train** 帧上训练，并在 **val** 计算 bbox IoU=0.5 指标。checkpoint 包含游戏 ID、manifest SHA256、research_only 标记。封存的 **test** 帧不会在训练/调参时读取。
3. 使用同一批封存 **test** 帧，在 CPU 上分别预测 COCO 通用 Sports Ball 基线和新篮球专项权重。专项模型检查 manifest SHA256 和 sealed test game IDs 是否与训练时一致；不符合直接拒绝。
4. 固定 score≥0.25、IoU≥0.5 对照 precision/recall/F1，并检查每 100 张人工无球负例误报量。**不允许反复用 test 集挑选最佳阈值**。
5. 输出 `box_comparison.json`、`experiment.json`、checkpoint (research_only)、训练日志 JSON、旧新测试预测 JSON。每个主要文件记录 SHA256。失败也留下状态原因；没有任何自动模型发布动作。

## 4. 发布边界

所有模型报告均为篮球框检测指标。投篮事件准确率、投中成功与否、高光剪辑召回率是**不同指标**。即使本次新权重比 COCO 基线高，也只允许进入独立人工审核，`production_release=false` 始终保留。必须先在完全独立比赛上实测球框，再用人工出手时间真值衡量高光误报漏报，审核模型及素材使用许可后另行决定是否上线。

## 5. GitHub Actions 验收

`.github/workflows/v09-real-dataset-gate.yml` 使用真实 Wikimedia Commons 传球和罚球视频重新提取此前四帧人工框，执行数据预检并**证明它会正确 BLOCKED**：两场、4 个正例、0 个无球负例、无完整复核记录。另一组有真实 JPEG 编码、但由代码生成的**合成假框**拥有超过 8 场、240 帧且数量足够，也同样因标明 synthetic 被 BLOCKED。两套测试只存 JSON 报告，不分发原视频、派生帧或权重。

“工程自动测试通过”不是“训练出高精度篮球模型”。真正改进要依靠你上传、标注和授权的多场比赛，不应伪造数字。
