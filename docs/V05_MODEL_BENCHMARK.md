# BasketCut V0.5 — 三集合隔离、同场模型对照和上线门禁

## 背景

V0.3 使用 4 张真实人工审核篮球框试训，独立验证框 TP=0 / FP=110 / FN=2。V0.4 免费 CPU 抽帧工具能减少找样本的操作，但其时间戳**不是篮球框真值**。V0.5 专注训练数据真实性、对照方法和发布拦截，不把工程演示写成准确率改善。

**现状：没有 6 场以上可直接训练的、经球馆授权并完成手工球框标注的比赛。** 这一阶段不会伪造样本，也不会将试验权重上线。

## 1. 完整篮球数据采集

登录 Django → 比赛详情 → 篮球框标注 → 免费 CPU 抽帧 → 人工判断有球/无球。审核无球画面尤其关注橙色球衣、广告牌、地板、球网和看台。不要把“识别不到球”当“无球”。

采集前确认视频许可与涉及人员授权，确认训练用途。至少收集 **6 场**不同比赛才能运行三集合导出；达到基础覆盖评审门槛的建议目标是 **8 场、至少 120 个篮球正例、40 张明确无球负例**，测试集至少有 30 个篮球正例、20 张明确无球负例。此为初期最低工程门槛，不是商业性能标准。

## 2. 三集合导出（按比赛隔离）

```bash
python manage.py export_ball_dataset \
  --owner-id 1 --out /tmp/basketcut-v05-dataset \
  --three-way --val-ratio 0.20 --test-ratio 0.20
```

产物：`images/train`、`images/val`、`images/test`，每套配套 YOLO `labels`；`manifest.json` 标明每张图片的 game_id、split、SHA256；`quality_report.json` 给出分区覆盖检查。至少 6 场才能拆成 train、val、test 各最少 2 场；相邻帧不会跨集合。

导出不足覆盖目标时仍然可以供**研究**，报告会标 `data_coverage_ready=false`，不能以此批准生产。旧版默认 `export_ball_dataset` 的两集合用法不变。

## 3. 研究用 CPU 训练（只读 train/val）

```bash
python -m highlights.vision.train_specialist \
  --dataset /tmp/basketcut-v05-dataset \
  --out /tmp/basketcut-v05-specialist.pt \
  --epochs 2 --max-steps 100 --device cpu
```

PyTorch/Torchvision 是独立可选环境，Django Web 及默认 Worker 不会额外安装大权重。训练器不得读取 test 图像或标签，输出的 checkpoint 始终标记 `research_only=true`。

## 4. 同一批封存测试帧上分别预测

```bash
python -m highlights.vision.predict_boxes_v05 \
  --dataset /tmp/basketcut-v05-dataset \
  --model coco-mobile320 --out /tmp/baseline.json

python -m highlights.vision.predict_boxes_v05 \
  --dataset /tmp/basketcut-v05-dataset \
  --model specialist --checkpoint /tmp/basketcut-v05-specialist.pt \
  --allow-research --out /tmp/challenger.json
```

所有模型在 **相同 test 图片** 上执行，输出归一化框和分数。若缺少预测、格式错误、预测框超出画面、图片 SHA256 不一致或 train/test 有重复图像，评测会拒绝或阻止进入下一审核阶段。

这些命令不计算投篮命中率：仅评估框的位置 IoU。

## 5. 对比与门禁

```bash
python -m highlights.vision.compare_v05 \
  --dataset /tmp/basketcut-v05-dataset \
  --baseline /tmp/baseline.json \
  --challenger /tmp/challenger.json \
  --out /tmp/basketcut-v05-comparison.json
```

报告对比篮球框 Precision/Recall/F1 (IoU≥0.5, score≥0.25)、负样本上每 100 帧的误检框、新旧 F1 差距，并审查基本数据覆盖、训练与测试图片 SHA 是否重叠。初始门禁建议 Recall ≥ 0.70、Precision ≥ 0.75、负样本每 100 帧误检框 ≤ 15、新模型 F1 至少提升 0.03。

即使通过所有数值条件，`production_release` **永远为 false**；只表示可提交**独立人工复核**。必须另行完成数据许可、真实长视频投篮事件召回率、隐私合规与上线审核。

**最终测试集不应用于多轮调参！** 如根据测试结果反复修改阈值/结构，测试集不再独立，必须重新从未接触过的比赛中留出一组最终盲测。

## 6. 实验演示与实际能力的区别

`scripts/synthetic_v05_compare_smoke.py` 可在 CPU CI 创建八场“合成占位字节 + 假框”用于测试隔离与汇总数学：即使挑战者 100% 正确，因标记 `synthetic=true`，门禁会给出 `eligible_for_independent_human_review=false`，绝不自动发布。该脚本**不生产 JPEG、不训练模型**，只验证程序。

真实篮球样本仍需在已授权的录像上由人工创建，不得把 CI 合成例子计入训练集。

## 7. 后续实际工作

收集至少 8 场来自不同机位和光照的授权比赛并逐帧完成正/负标签；使每一分区有正例和负例。先用上面的对照计算篮球框效果，再用 V0.2 的完整人工投篮时间真值报告衡量视频高光候选性能（包括漏检和误报）。未通过的权重不能默认启用。
