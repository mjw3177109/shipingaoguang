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
