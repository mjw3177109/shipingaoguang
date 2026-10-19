# BasketCut V0.3 专项篮球框模型：可执行操作手册

## 目标与边界

- **输入**：已经获得授权、以较固定机位拍摄的篮球比赛录像。
- **训练标签**：人工用矩形框标记每个选定时间帧里的篮球；看不到篮球时，主动标注“本帧无球”（负样本）。
- **学习目标**：辨认远景画面里的篮球（object detection），**不是**投篮动作、得分、助攻或个人身份识别。
- **默认生产链路不变**：Django 原有音频/CPU 颜色分析、人工审核、FFmpeg 合成全部保留。专项模型为独立可选推理 CLI，不会自动覆盖已经使用的规则算法。
- **上线门槛**：至少多场真实篮球比赛，按整场比赛划分 train/val，验证集手工审查框定位 TP/FP/FN，并在另一组未参与调参的视频做投篮候选盲测，才讨论默认使用专项模型。

## 1. Django 逐帧标注

1. 打开比赛详情，点击 **篮球位置框标注 / 打开标注工作台**。
2. 视频暂停，点击 **使用当前播放时间**，再点 **载入该时间帧**。
3. 在图片上拖动鼠标框住篮球，点击保存。对于没有篮球的帧，勾选 **本帧确认无球** 保存为负样本。
4. 必须同时收集有球和无球的不同情况：近景、远景、灯光、遮挡、橙色球衣、地板与篮圈。
5. 完成这一场后，在页面填写明确授权、来源和权利依据，并确认允许用于模型训练。
6. 若增删/覆盖框，训练许可状态自动撤销，需要复核后重新确认。

训练集导出只读取 **明确授权的比赛**。未经过网页授权的记录不会被导出。训练授权不能替代适用的个人信息、肖像与视频拍摄同意。

## 2. 单一球类 YOLO 数据导出（无需额外 pip 大包）

最少需要 2 场标注且授权的比赛，按 **整场比赛** 划分 train/val；禁止把同一场相邻帧随机分到两个集合。

```bash
python manage.py export_ball_dataset --owner-id 1 --out /tmp/basketball-dataset
```

`--owner-id` 是训练数据所有者的 Django 用户 ID；命令只在管理员有权限的可信服务器上运行。
可加 `--game-ids 1 2 3` 限定训练集包含哪些比赛。输出目录必须为空，避免旧数据污染。
输出 `images/train`、`images/val`、`labels/train`、`labels/val`、`manifest.json`、`data.yaml`。

注意：YOLO 指的是 **标注文件格式**，不意味着使用 Ultralytics 包、权重或其商业许可。

## 3. Torchvision CPU 专项微调

建议单独创建 Python 环境，避免 Torch 占用 Django Web 容器。使用 torchvision COCO 预训练骨干网络，替换分类头为 `背景 + 篮球` 两类，冻结骨干，只训练检测头；后续在 GPU 上按真实数据量升级完整微调。

```bash
python -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
python -m pip install pillow
python -m highlights.vision.train_specialist --dataset /tmp/basketball-dataset \
  --out /tmp/basketball-specialist.pt --epochs 2 --max-steps 100 --device cpu
```

输出 `basketball-specialist.pt` 和 `basketball-specialist.report.json`。报告给出独立比赛验证集上的 **篮球框 Precision/Recall/F1**（IoU≥0.5），不是进球率或投篮事件召回率。

如果是第一次离线环境，没有 Torchvision 预训练权重缓存，运行需联网下载权重；仅限工程流程测试可以使用 `--no-pretrained`，但这**不能代表训练出了有效篮球识别器**。

## 4. 免费 CPU 推理并导回审核

```bash
python -m highlights.vision.cpu_pretrained --video match.mp4 \
  --checkpoint /tmp/basketball-specialist.pt \
  --config game-123-hoops.json \
  --out basketball-candidates.json --arch mobile320 --tiles 4
```

因为训练好的模型使用的是单篮球分类头，代码会加载 checkpoint 的类别和架构信息，并使用对应的类别 ID。**如出现空候选，必须如实显示，不可自动声称成功**。推理结果使用此前的 V2 JSON，可回到同一场比赛 Django 页面导入、人工逐条审核，再走原 FFmpeg 导出。

`game-123-hoops.json` 必须从同一场比赛详情下载，能防止不同比赛时长/编号的结果混用。

## 5. 正式评测的正确做法

- 篮球框级：held-out 比赛上的标注框 IoU≥0.5，对比人审框统计 TP/FP/FN。
- 投篮候选级：用原有 `highlights.vision.evaluate` 配合 **完整人工出手时间点 ground_truth.json** 算候选 Precision/Recall 和每 10 分钟误报。
- 同一场不可以既用于超参选择又当最终盲测；短短 2 段视频无法建立生产所需统计置信。
- 免费 GitHub Actions CPU 的训练演示（如果启用）只做“训练器能执行并保存 checkpoint”验收；真实检测水平不能从合成数据得出。

## 6. 免费公开数据来源（实验用途）

- <https://commons.wikimedia.org/wiki/File:Basketpass.webm>：传球素材，CC0。
- <https://commons.wikimedia.org/wiki/File:VCB_FCB(4).webm>：罚球素材，Coentor，CC BY-SA 3.0。<https://creativecommons.org/licenses/by-sa/3.0/>

请遵守保留署名、相同方式共享等条款：对于图像和模型衍生物是否受 ShareAlike 约束，正式商业训练前先审查许可及法律适用。不擅自把这些视频改成新的授权。短片仅供早期工具与标注验收，不足以代表篮球馆整场比赛。

## 验收清单

1. Django 权限：只能框选、读取所属球馆视频。
2. 输入非法框（NaN、越界、过小）被拒绝。
3. 每次标注修改撤销训练许可确认。
4. 导出必须拥有至少 2 场有授权标注的比赛。
5. 训练和验证按完整比赛划分，不发生同场帧泄漏。
6. 模型初始权重与篮球专用 checkpoint 类别不混淆。
7. 输出只生成**待人工审核**的候选片段，不能冒充已进球。
