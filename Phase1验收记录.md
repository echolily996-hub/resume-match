# Phase 1 正式验收记录

日期：2026-10-05。

本次使用当前实际运行的前端 `http://localhost:5173` 和后端
`http://127.0.0.1:8000` 进行验收；没有使用临时 SQLite、临时后端或模拟响应。

## 实际执行与结果

| 验收检查 | 实际结果 | 结论 |
|---|---|---|
| 前端页面访问 | `http://localhost:5173` 可正常打开登录页 | 通过 |
| 登录交互加载状态 | 点击登录后输入框和按钮禁用，按钮显示“正在登录…” | 通过 |
| 登录失败反馈 | 页面显示“数据库暂时不可用，请稍后重试”及“重新检查连接” | 通过 |
| 经前端代理的健康检查 | `GET http://localhost:5173/api/v1/health` 返回 503 | 未通过 |
| 后端直连健康检查 | `GET http://127.0.0.1:8000/api/v1/health` 返回 503 | 未通过 |
| 后端错误结构 | 错误码为 `PERSISTENCE_FAILED`，响应含 request_id | 通过错误反馈要求；数据库仍阻塞 |
| Docker CLI | Client 29.8.2 可执行 | 通过 |
| Docker Compose CLI | v5.5.1 可执行 | 通过 |
| Docker Engine | Codex 执行环境访问 `dockerDesktopLinuxEngine` 命名管道被拒绝 | 阻塞 |

## 未能执行的验收项

数据库健康检查失败，因此以下项目不能形成有效验收结果：

- 两个预置账号分别登录及身份隔离；
- 正确密码登录、`/auth/me` 当前用户查询和刷新恢复；
- 错误密码的账号校验结果；
- Origin、CSRF、退出、旧 Cookie 撤销；
- PostgreSQL Alembic 实库迁移与初始化；
- 保留 `pgdata` 重建容器后的账号、会话和数据恢复。

## 宿主机正式复验结果

用户随后于宿主机完成正式验收，并提供以下实际结果：

- `web`、`api`、`db` 三个服务均为 healthy；
- Alembic 当前版本为 `0001_auth (head)`；
- 后端测试实际结果为 14 passed；
- HTTP smoke 实际通过；
- 浏览器实际验证登录、刷新恢复、退出、错误密码及两个账号操作正常；
- 保留数据卷强制重建后，两个预置账号数据保持一致。

上述宿主机结果补足了此前因 Codex 环境无法访问 Docker Engine 而未能执行的容器、
PostgreSQL、浏览器及数据卷验收项。此前 503 记录保留作为第一次受阻的真实历史，
不再代表最终验收状态。

## 验收结论

Phase 1 **正式验收通过**。通过范围为基础前后端、PostgreSQL、Alembic、Docker
Compose、两个预置账号、登录／退出／当前用户、后端登录保护，以及账号数据在保留
数据卷重建后的持久化。Resume、Job、Analysis、Suggestion、Revision 等后续阶段功能
不属于本次通过范围。
