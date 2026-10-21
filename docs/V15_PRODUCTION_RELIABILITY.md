# BasketCut V1.5：生产可靠性增量验收

## 变化

1. Django 上传路由提前检查 Content-Length。大于 MAX_UPLOAD_BYTES + 1MiB 的请求在解析 multipart 前返回 HTTP 413；缺少 Content-Length 返回 411。此为请求长度预拦截，仍需要 Nginx/Caddy 的请求体与临时卷硬限制；并不能防止客户端伪造长度或恶意媒体，真正文件大小仍由 V1.2 view 校验。
2. PostgreSQL 真数据库，两个线程、各自 Django 客户端/数据库连接，抢占同一用户不同球馆上传额度；并验证同一账号跨球馆同时创建导出任务不会超过活跃额度。不同账号配额独立。
3. 真正通过 localhost TCP HTTP 将 **5MiB 以上的合成视频** 分块传给 Gunicorn/Django，核验持久文件 SHA-256、Duration 和过大声明直接 413。不是假请求 / RequestFactory，不会将完整文件读进 Python 请求内存。
4. 实际 fork Django CPU Worker，观察其 RUNNING 租约与 FFmpeg 子进程后发送 OS SIGKILL 到进程组；等租约到期后启动第二个真实 Worker，重新认领并真正渲染有效 H.264 MP4。已验证旧租约 token 无法再次写入。
5. 独立 GitHub Actions：.github/workflows/v15-production-reliability.yml，保留机器生成 JSON 和真实短出片；两条 job 均通过才算 V1.5 工程场景验收。

## 实测边界（非常重要）

- HTTP 测的是本机 Gunicorn 端口，未经过 Nginx，也不等于公网浏览器多 GB 上传、限速、断点续传或带宽性能。
- 上传的是 FFmpeg 生成的**合成视频**，不代表真实完整篮球比赛。
- Worker SIGKILL 是真实操作系统强杀；单机 SQLite 隔离测试，不代表多节点高可用。
- PostgreSQL 检查只验证同账号并发存储配额、导出队列，不等于高并发大容量峰值及物理卷硬配额。
- 验收脚本拒绝生产数据库，不要在真实生产上设置 BASKETCUT_V15_ISOLATED=YES 来绕过限制。
- 未取得独立核实权利的 60–120 分钟连续实战录像，因此仍**不得声称完整比赛实战识别、Precision/Recall/F1 达标**。

## 运行方式

新 PR 会自动触发 V1.5 工作流，合并至 main 后再次触发。查看 GitHub Actions Artifacts：

- basketcut-v15-postgresql-locking-audit（真实 PG 独立事务与并发测试日志）
- basketcut-v15-real-http-sigkill-audit（真实 HTTP/SIGKILL 检验 JSON 与 FFmpeg 成片）

查看 SQL 数据库、缓存和媒体卷容灾仍需生产隔离环境独立验证。

## 下一阶段交付边界

V1.6 再进行独立授权完整比赛至少 3 场的实战准确率，2 GiB/4 GiB 真反代上传、代理缓存盘使用、多人长视频并发及真实跨容器 Worker 中断恢复。录像不能未经授权进入公开 GitHub Actions。
