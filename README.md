# BasketCut AI｜篮球馆精彩瞬间自动剪辑 MVP

面向篮球馆的 **可运行** MVP：录像上传 → 音频峰值高光候选 → 人工审片及球员归属 → FFmpeg 导出球队/个人集锦。**音频峰值不代表进球识别**；精准进球/球员追踪属于后续视觉模型迭代。

## 快速启动（Docker）

要求：Docker Compose v2，足够的硬盘空间。

```bash
cp .env.example .env
# 修改 .env 里的 SECRET_KEY、POSTGRES_PASSWORD
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

打开 http://localhost:8000/accounts/login/ 登录。先新建篮球馆，再上传 MP4/MOV/MKV/WEBM 视频，点击“分析候选”，等待 worker 后台处理；在比赛页审核片段、填写球员标识（如 7号/张三），最后排队导出横屏 MP4。输出文件仅登录并具备该球馆权限的账号能下载。

使用 WhiteNoise 提供静态文件服务；生产环境请使用 HTTPS、关闭 DEBUG 并使用对象存储/专业反向代理。

查看 worker：`docker compose logs -f worker`。数据库迁移由 web 容器启动时执行。**不要公开暴露此示例服务**：上线前需 HTTPS、可靠的对象存储、CSRF 及代理安全配置、账号管理、监控备份，并请合规审查录像授权。

## 无 Docker 的本地开发

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
# 需要安装 ffmpeg/ffprobe 并确保在 PATH 中
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
# 新终端:
python manage.py runworker
```

本地 SQLite 默认可用，生产使用 PostgreSQL（`DATABASE_URL`）。此处是单 Worker 原型，后续可改 Celery/云端 GPU Worker。若上传后界面停留在“排队中”，检查 runworker 日志。

## 测试

```bash
python manage.py check
python manage.py test
```

CI 会运行 Django 测试、静态配置检查和迁移检查，未代替真实 GPU/长视频测试。

## 文件结构

- `basketcut/`：Django 配置、URL
- `highlights/models.py`：球馆、比赛、候选片段、导出任务
- `highlights/services.py`：FFmpeg 探测、音频峰值候选、视频拼接
- `highlights/management/commands/runworker.py`：后台任务工作器
- `highlights/views.py`：上传、登录校验、审核、导出、私有视频预览
- `templates/` / `static/`：无需前端编译的响应式控制台
- `docs/ROADMAP.md`：AI/GPU/Kaggle 研究与商业化路线

## 功能边界及后续

V0.1 的自动候选基于音频峰值（观众欢呼也会触发，安静进球可能漏掉），**没有篮球/篮筐检测、进球判断、球员自动追踪，不具备无人审片准确性**。测试优先用有授权的样例视频。之后接视觉模型，把模型输出统一为片段建议，保留人审。

Kaggle/Colab 免费 GPU 用于研发试验，不能保证长期可用，也不应作为商业 SaaS 永久在线 Worker。生产 GPU 可接国内按需计费 GPU，视频素材与推理服务需遵守授权和适用的数据保护要求。

## 安全设计与尚待补齐

- 数据通过球馆 owner 范围过滤，录像/成片不通过公开 MEDIA_URL 直接暴露。
- 会校验文件扩展名、体积与 FFprobe 读取能力；未包含恶意视频隔离沙箱。
- 日志不要写入视频/人脸或身份敏感内容，勿将真实客户视频上传公开 GitHub。
- 删除比赛会删除相关本地视频文件；用户同意、保存期限、存储加密、审计日志、租户成员和计费需在正式商用前完成。
- 当前样例本地存储，不是高可用商用部署。

许可证：源代码暂未授予第三方使用许可；依赖遵循各自许可证。

## Kaggle 免费 GPU 视觉实验

`notebooks/kaggle_ball_probe.ipynb` 提供不依赖服务端常驻 GPU 的试验：Kaggle Notebook 设置 GPU，加上有授权比赛视频的数据集，按 Notebook 修改 `VIDEO_PATH`，得到 `candidates.json`。回到对应比赛页用“导入候选片段”导入，然后人工复核。注意：COCO 运动球检测不等于进球判定，这只是视觉候选接口的技术验证。Ultralytics 模型商用需要额外确认许可证。

## V0.2 GPU 视觉投篮候选（新增）

V0.2 引入每场比赛篮筐框选、Torchvision sports ball GPU 检测、篮筐周围时序候选、带比赛 ID/时长校验的 JSON 回传。**不是进球成功识别，不能省略人工审核。**

操作说明：[`docs/V0.2_VISION_GUIDE.md`](docs/V0.2_VISION_GUIDE.md)。
免费 GPU Notebook：[`notebooks/kaggle_shot_candidates_v02.ipynb`](notebooks/kaggle_shot_candidates_v02.ipynb)。

原 V0.1 的 CPU 声音峰值/手动选片/FFmpeg 导出保留不变；Kaggle 仅为手动研发流程，不支持自动调用 Kaggle 作为收费平台 GPU Worker。

## V0.2 实战评测 + CPU 调参闭环

新支持在比赛播放中人工记录投篮尝试并下载 `ground_truth.json`，从 Kaggle 视觉推理保存 `ball_observations.json` 以后，可以在 **CPU** 上生成 Precision / Recall / F1、每10分钟误报、耗时与费用报告，以及重复搜索不同阈值而不重新占用 GPU。支持最多 100 场的独立汇总。

详细说明：[`docs/V0.2_BENCHMARK_GUIDE.md`](docs/V0.2_BENCHMARK_GUIDE.md)。

注意：`benchmarks/example_*.json` 只有模拟数据，CI 通过不证明篮球事件检测的真实效果。目前没有自动连接 Kaggle 账号或支付算力，真实 GPU 模型和球馆视频仍需独立验证。

## V0.2-CPU 免费视觉候选（不需要 Kaggle、显卡或模型权重）

在比赛详情页，点击 **CPU 视觉候选（免费）** 即可让现有后台 worker 使用 OpenCV 在普通云 CPU 上识别橙色近圆物体的运动。提前框选篮筐，会优先推荐篮筐附近活动；没有篮筐框选时只给出全场球状物体活动候选。

⚠ **不是训练好的篮球动作识别模型，也不能判断投篮命中、助攻或球员身份。** 橙色球衣、光照变化可能误报；不是橙色的篮球或远距离小球可能漏检。**候选为零时不能自动伪造成功**，可手动打点完成视频生产。保留 V0.1 声音候选和人工审核。

独立 CPU 命令：

```bash
pip install -r requirements-cpu.txt
python -m highlights.vision.cpu --video video.mp4 --out candidates.json
# 已从界面下载篮筐标定 JSON：
python -m highlights.vision.cpu --video video.mp4 --config game-1-hoops.json --out candidates.json
```

真实篮球视频 GitHub Actions 测试见 `docs/REAL_VIDEO_SMOKE.md`。固定机位的完整比赛素材与短片特写的表现可能不同，不可把测试片段的结果当作篮球馆的实际识别率。

## V0.2-CPU 实战验证：传球 vs 罚球、预训练模型（最新）

本轮新增两段**真实公开许可**的篮球比赛视频 CPU 对比，保留逐帧预览、颜色模型识别目标标记、时间段 JSON 与测试证据。

- [颜色规则对比与误检可视化](docs/FREE_THROW_CPU_TEST.md)：发现传球、罚球均有 1 条颜色活动候选，不能区分投篮。
- [真正的预训练 CPU 目标检测实验](docs/PRETRAINED_CPU_MODEL.md)：Torchvision COCO `sports ball`，可选 MobileNet320 / ResNet50 / 4 分块裁剪 / 手动篮筐 ROI。
- Web 默认仍使用轻量 OpenCV 或音频候选；大型 Torch 权重**不安装在基础 Docker 容器**，需独立 CPU 推理环境运行命令并导入待审 JSON。
- 真实罚球远景中检测框有明显离篮筐的情况；不把该结果描述成进球识别成功。

查看 GitHub Actions 的 `Real Free Throw CPU Comparison` 和 `Free CPU Pretrained Ball Model Comparison` 工作流结果。真实视频下载于测试运行期，仓库不存原片。

## V0.3 篮球专项 CPU 模型与人工位置框训练数据

不覆盖 V0.1/V0.2 原有功能，新增加：

- 比赛详情的 **篮球位置框标注工作台**：指定时间帧画篮球框或标“无球”，按球馆账号权限隔离。
- **训练授权确认**：修改标注后自动撤销授权状态。管理员运行 `python manage.py export_ball_dataset --owner-id <ID> --out <EMPTY_DIR>`，只导出确认授权的标注。
- 按整场比赛拆成 train/val，而不是把同一录像的临近帧同时算训练和验证。
- **单篮球类别** Torchvision Faster R-CNN MobileNetV3-320 CPU 训练、checkpoint 存储和可选 CPU 推理；未审核权重被强制标为 `research_only`，正式推理默认拒绝，研究必须显式 `--allow-research-checkpoint`。
- 保留现有人工候选审核与 FFmpeg 处理流程。**没有自动判定进球或已获商用认证的模型**。

[完整标注、CPU 训练和评测指南](docs/BASKETBALL_SPECIALIST_V03.md) · [真实两段比赛、四个目视审核球框的小规模 CPU 训练实验](docs/REAL_4_FRAME_PILOT.md)。

**实际结果提醒**：仅四个真实球框的试验已经验证训练及推理工程链路，但两个留出框均未命中，发生大量误检，因此此专项 checkpoint 明确被拦截，不可以部署到篮球馆生产环境。下一阶段须扩充标注数据并进行独立盲测。

## V0.4：CPU 标注帧候选与难负样本采集

- 在球框标注工作台点击 **生成 24 个以内待审帧（CPU）**；由现有 Django Worker 执行，后台采样最多 96 帧，数据库只保存建议时间戳和理由，不自动确认篮球存在/不存在。
- 按橙色疑似物体、运动画面和普通背景分类推荐**需要人工审核的帧**，帮助找到橙色球衣、看台等误检干扰。标注并保存后，该帧建议自动从列表消失。
- `python manage.py audit_ball_labels --owner-id 1` 快速检查训练授权和已标注的正例/明确无球负例数量。
- `export_ball_dataset` 在原 YOLO 数据文件旁增加 `quality_report.json`，按整场比赛拆分后验证训练/验证均含正负样本及数量门槛。门槛通过**只意味着覆盖数量达到基本要求**，绝不等于模型精度通过。
- <https://github.com/mjw3177109/shipingaoguang/blob/main/docs/V04_CPU_ANNOTATION_LOOP.md> 中有完整操作、真实视频实验和约束。

## V0.5：模型对比与封存测试集（研究阶段）

本版在 V0.4 人工标注系统上新增 **按比赛隔离的 train/val/test** 导出、基础样本审查、**同一批测试帧**上的旧版 COCO 与篮球专项模型 CPU 对照、误检预算和**禁止自动上线**的研究门禁。默认声音/CPU 剪辑、审核和视频渲染链路不变。

- 用 `python manage.py export_ball_dataset --owner-id 1 --out /tmp/v05 --three-way` 导出，至少 6 场完成授权且具备标签的比赛。
- `python -m highlights.vision.predict_boxes_v05` 运行 CPU 预训练基线或研究专项权重；`python -m highlights.vision.compare_v05` 在完全一致的封存测试帧对比指标。
- 未标注的录像不能自动贴“无球”；跨比赛重复图片 SHA、漏测图片和不合格数据覆盖阻止下一阶段审核。
- 不管指标多高都只可以**提交人工审核**，输出 `production_release=false`，没有任何自动发布模型路径。

[完整使用教程和当前数据不足说明](docs/V05_MODEL_BENCHMARK.md)。CI 合成样本仅用于数学、路径和防作弊规则测试，**不代表新增真实篮球识别准确率**。

## V0.6：研究 CPU 轨迹候选 → 人工审核 → FFmpeg

- 在比赛详情新增独立 **CPU 研究模型观测导入**：必须是当前比赛的已完成 CPU 专项模型扫描、篮筐 ROI 一致、原视频首尾采样指纹一致、观测未截断。
- 从至少 3 帧时间连续、位移可信的球状物体轨迹生成`pending`候选。该分数只是目标检测置信度，**不是投篮/进球概率**。零候选不自动生成高光。
- 保留 V0.1–V0.5 的音频/颜色候选、人工标注、审核、视频导出等功能。Torch 仍只装在独立的研究 CPU 环境。
- 未通过 V0.5 的独立真实数据盲测之前，所有专项权重依旧为 `research_only`，**不得默认部署到生产**。
- [完整研究模型连接与操作说明](docs/V06_RESEARCH_OBSERVATIONS.md)。V0.6 GitHub Actions 使用真实公开视频+**合成观测**验证工程出片，不代表真实篮球动作识别准确率。

## V0.7：人工二次复核与严格训练数据导出

- 篮球框标注页支持 **保存并跳下一待审帧**、**回看原框** 和 **每条记录独立二次复核**，标签修改后复核状态自动失效。
- 保存复核人及时间，可查看完成进度并每页分页 50 帧，不会因为历史 500 条显示上限而看不到旧标签。
- 完全保留旧数据：所有历史球框默认`未复核`，绝不自动标记合格；旧版双集合研究导出仍有效。
- 可选严格导出：`python manage.py export_ball_dataset --owner-id 1 --out /tmp/v07 --three-way --verified-only`。该模式不接受未经完整二次复核的授权比赛，V0.5 同场评测亦要求审核记录。
- `python -m highlights.vision.review_readiness_v07 --manifest /tmp/v07/manifest.json --out /tmp/v07/readiness.json` 给出正负样本、复核量、覆盖门槛报告，**不等于模型精度**。
- 在 GitHub Actions 用许可明确的真实视频已选四帧历史球框进行数据就绪性审查，因无负例、无二次复核、样本不足，明确报告未达标，不声称高光识别提升。

完整操作和数据使用边界： [docs/V07_VERIFIED_LABEL_PIPELINE.md](docs/V07_VERIFIED_LABEL_PIPELINE.md)。

## V0.8 · 多场比赛数据采集看板 + CPU 分轮智能抽帧

- 球馆首页新增 **V0.8 训练数据采集看板**，横向比较本账号所有比赛的人工篮球框、真实无球负样本、复核完成量、授权状态、待审时间点。JSON 私有报告：`/ball-campaign/report.json`。
- V0.4 单轮固定24时间点的限制已改善：每次重新排队会使用不同采样时间网格，在普通 CPU 中从视频取样，不清空上轮未处理的建议；过滤已有标签和近重复点，最多保留300条未处理建议。
- 标注工作台可展示最多6张安全登录鉴权的真实帧预览（仅辅助查阅，**不是 AI 识别证据**）。
- 持续使用 V0.7 二次人工复核和授权后，再采用 `--three-way --verified-only` 严格导出。
- 代码已在许可明确的真实篮球片段上进行 CPU 两轮抽帧验证，验证的是**新时间点**，不代表实际识别到了篮球/进球。

具体部署与质量边界请看 [docs/V08_LABEL_CAMPAIGN.md](docs/V08_LABEL_CAMPAIGN.md)。

## V0.9 — CPU 专项模型训练与独立盲测实验（默认安全预检）

本阶段把 V0.7 的人工二次复核导出、Torchvision CPU 模型训练、V0.5 COCO 基线/专项模型封存测试评测连接成 **可执行的单一研究命令**：

- `python -m highlights.vision.experiment_v09 --dataset /data/dataset --out /data/check`：仅预检（无 Torch、无模型下载、无训练），检查真实有球/无球框、按比赛隔离 train/val/test、完整二次复核/权利依据、JPEG 和 YOLO 标签、SHA256、封存测试集完整性；数据不合格输出 `BLOCKED`。
- `python -m highlights.vision.experiment_v09 --dataset /data/dataset --out /data/experiment --execute --epochs 2 --max-steps 100`：**仅当预检通过**才真正用 CPU 训练、推理新旧模型并出对照 JSON 与 checkpoint，保持 `research_only`，没有自动商用发布动作。
- 训练 checkpoint 的数据集 manifest SHA256 和封存测试比赛 ID 由 CPU 推理时再校验，防止换数据集、串场或训练/测试泄漏。
- 免费 GitHub CI 使用真实公开视频四帧标注验证**正确阻断样本不足**，另外使用明确为合成的数据验证即使数量、框、复核字段看似齐全也不能冒充真实训练条件。

完整步骤与报告解释：[docs/V09_CPU_EXPERIMENT.md](docs/V09_CPU_EXPERIMENT.md)。目前**并没有足量真实人工复核的数据，因此没有能够证实准确率提升的新篮球专项模型**。
