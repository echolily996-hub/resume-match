# Phase 1 实际测试记录

日期：2026-10-05。范围仅为基础前后端、数据库迁移、预置账号及登录保护；未实现 Resume、Job 或 AI 分析。

## 实际修改

- frontend：Vue 3 + TypeScript + Vite 骨架、登录与退出页面、会话刷新恢复、加载／成功／错误反馈、同源 API 客户端、多阶段 Dockerfile 和 Nginx 代理。
- backend：FastAPI、SQLAlchemy、Alembic 初始迁移、users/sessions 两表、两账号幂等初始化、登录／退出／当前用户、可复用 current_user 后端保护、Origin／CSRF 校验、Argon2id、会话哈希、统一安全错误反馈。
- compose.yaml：web/api/db 三服务、数据库健康检查、迁移与 seed 启动顺序、PostgreSQL 命名卷；数据库与 API 不直接暴露宿主机端口。
- .env.example、.gitignore、.dockerignore、依赖锁文件、README、两个宿主机验证脚本。
- 未创建实际 .env，未配置或写入真实密钥。账号密码和数据库示例值均为公开本地测试值。

## 实际运行与结果

| 检查 | 实际结果 | 结论边界 |
|---|---|---|
| uv pip compile --universal | 成功，32 个锁定依赖 | 不代表 Linux 镜像已构建。 |
| 依赖安装 | Python 依赖安装成功；npm install 安装 50 个包并生成 package-lock.json | 当前 Windows 本地安装。 |
| npm run typecheck | 成功 | Vue／TypeScript 类型检查通过。 |
| npm run build（最终） | 成功；Vite 转换 12 个模块并生成 dist | 实际前端生产构建；没有执行浏览器点击测试。 |
| python -m pytest -q（最终） | 13 passed，1 条依赖弃用警告 | 使用临时 SQLite 与 TestClient，非 PostgreSQL／Docker 验收。 |
| 临时 SQLite Alembic upgrade head 与 seed | 成功 | 迁移代码实际执行；正式 Compose 仍固定 PostgreSQL。 |
| uvicorn 本地 HTTP + tests/http_smoke.py | 退出码 0；登录、两个身份、me、CSRF、退出及旧 Cookie 撤销检查通过 | 实际 TCP HTTP 请求，使用临时 SQLite；没有 Nginx／PostgreSQL。 |
| python -m compileall -q app migrations tests | 成功 | Python 语法编译检查。 |
| python -m alembic upgrade head --sql | 成功，生成 PostgreSQL UUID、TIMESTAMP WITH TIME ZONE、索引和外键 SQL | 离线生成，未执行 PostgreSQL 实库迁移。 |
| docker compose --env-file .env.example config --quiet | 成功，退出码 0 | 配置解析检查，无 Engine 连接，不代表部署成功。 |
| PowerShell Parser 检查 Test-Phase1.ps1 | 成功，无语法错误 | 仅语法检查。 |
| powershell.exe -NoProfile -File scripts/Test-Phase1.ps1 | 被执行策略拦截，退出码 1：running scripts is disabled on this system | 未运行脚本中的 HTTP 断言；待宿主机验证。未修改执行策略。 |

pytest 覆盖：

1. 两账号存在、身份不同、密码为 Argon2id、重复 seed 不改账号 ID／密码。
2. 未登录与伪造 Cookie 被拒绝。
3. 登录、HttpOnly／SameSite Cookie、当前用户、会话恢复、数据库只存 token 哈希。
4. 错密码及不存在用户不能创建会话。
5. 退出清 Cookie，并在服务端撤销旧 token。
6. 错误 Origin、缺 CSRF 被拒绝且不误撤销会话。
7. 过期会话被拒绝。
8. 再登录轮换会话，旧 token 失效。
9. 两账号各自返回正确身份。
10. 后端 current_user 依赖保护测试专用路由；该路由不加入产品应用。
11. 输入错误响应不回显密码。
12. 健康检查及仅存在 Phase 1 产品接口。
13. 数据库故障返回 503，不假报登录成功或泄露数据库参数。

## 过程中发现与修正

- 第一次 pytest 因沙箱默认临时目录权限错误出现 12 项 setup error，尚未执行断言。将测试临时目录明确放入工作区后正常运行，不修改系统权限。
- 之后 11 项通过、1 项失败：退出接口返回注入的 Response 时状态码未显式设定。修正为 204 后重跑，最终 13 项全部通过（含新增数据库故障检查）。
- Vite 初次构建在 Node fs.realpathSync.native 上出现 EPERM。普通读取和非 native realpath 在同一工作区文件正常。使用 Vite 官方 resolve.preserveSymlinks 配置按原始项目路径解析后构建成功，没有修改系统权限或扩展文件访问范围。项目无链接包，Docker 与本地使用同一配置。
- pytest 的 1 条警告为 Starlette 对 AnyIO BlockingPortal 别名的弃用提示，不影响本次测试结果。

## 待宿主机验证

| 项目 | 状态 |
|---|---|
| Docker 镜像拉取、构建、Compose 启动和健康 | 待宿主机验证 |
| PostgreSQL 实库 Alembic 迁移和预置账号初始化 | 待宿主机验证 |
| 经 Nginx → FastAPI → PostgreSQL 的真实 HTTP 流程 | 待宿主机验证 |
| 保留 pgdata 重建容器后账号 ID／会话恢复 | 待宿主机验证 |
| 浏览器登录、刷新、退出、错误及加载交互 | 未执行，待宿主机验证 |
| Windows PowerShell 验证脚本实际运行 | 受本轮执行策略限制，待宿主机验证 |

宿主机此前 Docker version/info 已由用户提供成功输出；当前 Agent 用同一 docker.exe 仍因 Docker Engine 命名管道权限被拒绝。按用户最新授权，这不阻止开发，也不把容器验收冒充已通过。

## 宿主机命令

见 README 的 Docker 启动、容器内 tests/http_smoke.py 和保留卷重建检查。Python HTTP 验证脚本本轮实际跑通；可直接在容器内执行，无需改变宿主机 PowerShell 执行策略。它的宿主机 PostgreSQL 运行结果仍待实际执行。

本阶段没有任何 Phase 2 功能、模型调用、仓库 SHA 确认、材料上传或交卷操作。
