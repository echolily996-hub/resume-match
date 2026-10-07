# 简历与岗位匹配助手

一个面向求职简历优化场景的完整 Web 应用。

用户可以保存简历与岗位描述，调用真实大模型分析岗位要求与简历证据，在不虚构经历、技能、职责和量化成果的前提下获得安全修改建议，并通过 Draft、显式保存、版本历史、预览和 Markdown 导出完成完整的简历修改流程。

项目使用真实前端、后端、数据库和 Docker Compose 部署。

---

## 一、核心功能

当前版本已经完成以下完整流程：

```text
创建简历
→ 创建岗位
→ 发起真实模型分析
→ 查看岗位要求
→ 查看简历证据
→ 查看匹配状态和原因
→ 查看补充确认问题
→ 采纳 / 忽略安全修改建议
→ 手工编辑 Draft
→ 显式保存
→ 查看保存历史
→ 恢复历史版本
→ 预览已保存版本
→ 导出 Markdown
```

主要功能包括：

- 预置账号登录与退出；
- Resume 创建、查看和历史记录；
- Job 创建、查看和修改；
- 每次 Analysis 保存独立的 Resume / Job 快照；
- 后端异步调用真实 OpenAI-compatible 模型；
- 岗位要求拆解；
- 简历连续原文证据提取；
- `reflected` / `not_reflected` / `needs_confirmation` 三态判断；
- 判断原因和补充确认问题；
- 模型状态、错误和显式 retry；
- 安全修改建议；
- suggestion 接受 / 忽略；
- Draft 与原始 Resume 分离；
- 手工编辑 Draft；
- 显式保存；
- immutable saved revisions；
- 历史版本查看；
- 历史版本恢复到 working draft；
- 已保存版本预览；
- Markdown 导出；
- 后端账号所有权校验；
- PostgreSQL 持久化；
- Docker Compose 一键启动。

---

## 二、技术栈

### 前端

- Vue 3
- TypeScript
- Vite
- Nginx

### 后端

- FastAPI
- Pydantic
- SQLAlchemy
- Alembic
- psycopg
- argon2-cffi

### 数据库

- PostgreSQL

### 测试

- pytest
- httpx
- Vue / TypeScript 类型检查
- Vite production build

### 部署

- Docker
- Docker Compose

---

## 三、快速启动

### 1. 准备环境变量

在项目根目录执行：

```powershell
Copy-Item .env.example .env
```

如果 `.env` 已经存在，不要覆盖。

然后编辑 `.env`，填写真实模型配置。

至少需要配置：

```env
MODEL_BASE_URL=
MODEL_API_KEY=
MODEL_NAME=
```

真实 API Key 只能保存在本地 `.env` 中。

**不要将真实 API Key 写入代码、README、`.env.example` 或提交到 Git 仓库。**

---

### 2. 启动项目

在项目根目录执行：

```powershell
docker compose up --build -d --wait
```

查看容器状态：

```powershell
docker compose ps
```

查看日志：

```powershell
docker compose logs --tail 100 api db web
```

启动成功后访问：

```text
http://localhost:8080
```

Compose 启动过程中会完成数据库健康检查、Alembic migration、测试账号初始化以及 API / Web 服务启动。

当前 Alembic head：

```text
0007_saved_revisions
```

---

## 四、测试账号

项目提供两个预置测试账号：

| 用户名 | 密码 |
|---|---|
| `demo_a` | `Demo-A-2026!` |
| `demo_b` | `Demo-B-2026!` |

这些账号仅用于本地验收，不是真实用户或生产凭据。

初始化逻辑只会创建不存在的测试账号，不会因为重新启动容器而删除或覆盖已有业务数据。

---

## 五、模型配置

后端通过 OpenAI-compatible API 调用模型。

主要环境变量：

| 环境变量 | 说明 |
|---|---|
| `MODEL_BASE_URL` | OpenAI-compatible API 根地址 |
| `MODEL_API_KEY` | 服务端模型 API Key |
| `MODEL_NAME` | 实际调用的模型名称 |
| `MODEL_RESPONSE_MODE` | 结构化响应模式 |
| `MODEL_TIMEOUT_SECONDS` | 单次模型调用超时时间 |
| `MODEL_MAX_OUTPUT_TOKENS` | 最大输出 token |
| `ANALYSIS_WORKER_ENABLED` | 是否启用 Analysis worker |

最终真实验收使用模型：

```text
qwen3.8-max
```

只有模型基础地址、API Key 和模型名称均正确配置后，系统才允许创建真实 Analysis。

模型未配置时不会返回伪造的固定分析结果，而会返回明确的配置错误。

模型密钥只在服务端使用，不通过 `VITE_*` 等前端变量暴露。

---

## 六、Analysis 与事实边界

每一次 Analysis 都保存创建时的：

- Resume snapshot；
- Job snapshot。

因此后续修改 Job 或 Draft 不会改变已经完成的历史 Analysis 输入。

模型返回的主要内容包括：

- 岗位要求原文；
- 简历证据；
- 匹配状态；
- 判断原因；
- 补充确认问题。

匹配状态包括：

```text
reflected
not_reflected
needs_confirmation
```

系统遵循一个核心原则：

> 简历没有提供的信息不能因为岗位要求中存在，就自动视为候选人已经具备。

例如：

- “了解 Linux”不能自动升级为“熟练 Linux”；
- “参与”不能自动升级为“主导”；
- 没有量化成果时不能自动生成百分比；
- 没有 PostgreSQL / Redis / Docker / Kubernetes 经历时不能自动加入这些技能；
- 信息不足时应使用 `needs_confirmation` 或其他保守状态。

补充确认问题独立展示，不会自动进入简历正文。

---

## 七、安全修改建议

Analysis 完成后，后端根据模型结果生成 suggestion。

自动可采纳修改采用受约束的事实守恒策略，支持两类建议：

- 兼容把已有原文整理为 Markdown 条目；
- 在不改变事实的前提下精简措辞、调整语序，并突出原文已有的岗位相关信息。

安全 suggestion 必须满足事实约束，例如：

```text
原文：
使用 Python 和 FastAPI 开发 REST API。

建议：
基于 Python 与 FastAPI 开发 REST API。
```

后端会先检查原始段落、简历证据、数字、英文技术或产品词、职责词和能力强度词，再对自然语言改写执行独立的 suggestion 事实审校。只有确定性校验和事实审校都通过时，建议才能直接写入 Draft；审校不确定或不可用时会阻止自动采纳。

若 suggestion：

- 缺少原文支持；
- 对职责进行升级；
- 新增技术栈；
- 新增年限；
- 新增公司或项目；
- 新增量化成果；
- 对事实进行无法验证的扩展；

则不会作为可直接采纳的安全建议进入 Draft。该机制用于尽量阻止事实幻觉，不能替代用户对最终简历内容的核对。

当没有可安全直接修改的内容时，系统可以正常显示：

```text
本次分析没有可安全直接改写的内容。信息缺口请先补充确认。
```

这属于正常安全降级，不代表 Analysis 失败。

---

## 八、Draft、保存与版本历史

原始 Resume 与修改稿严格分离。

### 原始 Resume

原稿创建后保持不变。

接受 suggestion、手工修改 Draft 或恢复历史版本都不会修改原始 Resume。

### Working Draft

用户可以：

- 接受 suggestion；
- 忽略 suggestion；
- 手工修改正文。

这些操作作用于当前 Draft。

### 显式保存

只有用户明确执行保存操作后，才会生成新的 saved revision。

保存时：

- working draft 正文被保存；
- 当前 suggestion decisions 生成服务端快照；
- revision 版本递增；
- 已有历史 revision 不会被覆盖。

### 并发冲突

Draft 使用 `edit_version` 防止旧页面静默覆盖新修改。

版本过期时后端返回：

```text
409 VERSION_CONFLICT
```

---

## 九、历史版本恢复

用户可以查看历史 saved revision，并把历史正文恢复到当前 working draft。

restore 的语义为：

```text
历史 saved revision
        ↓
恢复正文到 working draft
        ↓
尚未保存
```

restore：

- 会更新 working draft；
- 会递增 `edit_version`；
- 不会修改当前 `saved_version`；
- 不会自动生成新 revision；
- 不恢复历史 suggestion decision 状态。

只有用户之后再次执行显式保存，才会生成新的 saved revision。

---

## 十、预览与 Markdown 导出

正式 preview 和 export 都读取**已保存版本**，而不是当前未保存的 working draft。

因此：

```text
修改 Draft
但未保存
        ↓
preview 不变
export 不变
```

显式保存后：

```text
working draft
        ↓
saved revision
        ↓
preview / export
```

没有任何 saved revision 时，preview / export 返回：

```text
409 NO_SAVED_REVISION
```

Markdown 导出内容不会包含：

- 模型分析解释；
- 未确认问题；
- 被忽略 suggestion；
- 未保存 Draft 修改。

FINAL-C 由应用实际导出的 Markdown 示例：

```text
examples/sample-export.md
```

该文件已经与 FINAL-C Saved Revision v1 核对一致，其中包含实际采纳后的 PostgreSQL 内容级改写。

---

## 十一、账号与权限隔离

数据权限由后端执行，不依赖前端隐藏按钮。

所有 Resume、Job、Analysis、Draft、SavedRevision 等业务资源都会根据当前登录账号执行 owner 校验。

例如：

```text
demo_a 创建的数据
        ↓
demo_b 直接访问对应 API
        ↓
404
```

通过统一返回资源不存在，避免向其他账号泄露资源是否真实存在。

`owner_id` 不能由前端任意指定。

---

## 十二、认证与安全

当前认证机制包括：

- Argon2id 密码哈希；
- 随机会话 token；
- HttpOnly Cookie；
- SameSite=Lax；
- 服务端仅保存 session token 哈希；
- CSRF token；
- Origin 校验；
- 会话过期；
- logout 后会话撤销。

生产 HTTPS 部署时应设置：

```env
COOKIE_SECURE=true
```

本地 HTTP 验收环境使用：

```env
COOKIE_SECURE=false
```

应用不会在正常 API 错误中返回：

- 原始密码；
- session token；
- API Key；
- 数据库异常正文。

---

## 十三、数据库与持久化

PostgreSQL 数据使用 Docker volume 持久化。

正常停止：

```powershell
docker compose down
```

重新启动并保留数据：

```powershell
docker compose up -d --wait
```

最终验收还执行了：

```powershell
docker compose up -d --force-recreate --wait
```

容器重新创建后实际验证：

- Resume 仍存在；
- Job 仍存在；
- Analysis 仍存在；
- Analysis snapshots 仍存在；
- Draft 仍存在；
- suggestion decisions 仍存在；
- saved revisions 仍存在；
- preview 正常；
- export 正常；
- restore 正常。

### 注意

持久化验收时不要执行：

```powershell
docker compose down -v
```

`-v` 会删除数据库 volume。

---

## 十四、数据库迁移

当前最终 migration head：

```text
0007_saved_revisions
```

检查命令：

```powershell
docker compose exec api python -m alembic current
```

---

## 十五、测试与验证

### 后端自动化测试

执行：

```powershell
docker compose exec api python -m pytest -q
```

最终验证结果：

```text
59 passed, 3 non-blocking warnings
```

3 个 warning 均为非阻断 warning，不影响测试通过。

### 前端

前端最终执行：

```text
vue-tsc
vite build
```

结果：

```text
passed
```

### Docker 状态

执行：

```powershell
docker compose ps
```

`docker compose up -d --build` 成功，最终验收时 `db`、`api`、`web` 均为 healthy。

---

## 十六、最终验收素材

项目包含两组固定虚构验收材料。

### FINAL-C：较匹配与内容级改写场景

```text
examples/resume-match.md
examples/job-match.md
```

用于验证：

- 多项岗位要求已有简历证据；
- 真实模型生成事实守恒的内容级 suggestion；
- 接受 / 忽略；
- Draft；
- 保存；
- preview；
- export。

最终实际模型调用：

```text
model: qwen3.8-max
attempt_no: 3
duration_ms: 34464
provider_request_id: e99636a6-0152-92e5-b169-add79f37f9c2
analysis_id: df5dd27c-9185-4004-a5e9-9d1511eea21b
```

FINAL-C 实际采纳 Suggestion `de864cc4-f082-4c43-89f8-aea793f9889e`（Requirement `221e92fb-3cb1-405f-8a52-119a7549ca99`），将 PostgreSQL 原文改写为“熟悉 PostgreSQL 关系型数据库，能够进行数据表设计、常见查询和事务处理。”；Suggestion `4db2e2f5-1f4e-46ff-8f95-b40f1724c89b`（Requirement `b1807579-2caa-4be0-9364-b91201e738ff`）被忽略。Docker Suggestion `56a4c670-107e-4978-a117-891a26b4d9f4` 当前仍为 pending。

Draft `79a108cf-1cb4-47e4-8c78-50b06ed3b497` 已显式保存，当前 `edit_version=3`、`saved_version=1`；Saved Revision v1 ID 为 `7358377a-0853-4e94-80e0-5ec603c7b0cb`，当前 Draft 与该保存版本完全一致。

### FINAL-D：信息缺口与事实边界场景

```text
examples/resume-gap.md
examples/job-gap.md
```

用于验证：

- 缺失技能；
- 能力强度差异；
- 有限职责；
- 无量化成果；
- `needs_confirmation`；
- Redis / Kubernetes 实际经验缺口；
- 独立负责与量化成果缺口；
- 不编造、不升级事实；
- 非法简历引文按 requirement 安全降级。

最终 Analysis ID：

```text
26928b36-39d7-4053-ae0b-6128469f5c58
```

该 Analysis 第一次因 `INVALID_RESUME_QUOTE / QUOTE_CROSSES_PARAGRAPH` 失败；修复后 attempt 2 使用 `qwen3.8-max` 成功，耗时 `16989 ms`，provider request id 为 `977d089e-95e4-98d9-9118-67277ca2a895`。

---

## 十七、最终验收报告

完整最终验收记录：

```text
docs/最终验收报告.md
```

其中包含：

- FINAL-C 完整流程与内容级 suggestion；
- FINAL-D 信息缺口与事实边界；
- 账号权限隔离；
- Draft / saved revision；
- restore；
- preview / export；
- Docker 持久化；
- 最终测试状态；
- 已知限制。

---

## 十八、项目目录中的主要交付材料

```text
README.md
.env.example
compose.yaml

examples/
├── resume-match.md
├── job-match.md
├── resume-gap.md
├── job-gap.md
├── 测试账号与素材.md
└── sample-export.md

docs/
└── 最终验收报告.md
```

此外项目包含：

- 前端源码；
- 后端源码；
- Dockerfile；
- Alembic migrations；
- 自动化测试；
- 数据库初始化逻辑；
- 模型调用实现。

---

## 十九、开源组件与自行实现范围

项目使用的主要开源组件包括：

- Vue
- TypeScript
- Vite
- Nginx
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic
- psycopg
- argon2-cffi
- PostgreSQL
- pytest
- httpx
- Docker / Docker Compose

前端 npm 依赖版本由 lockfile 固定，Python 依赖由项目依赖文件固定。

本项目自行实现的业务部分主要包括：

- 登录与会话流程；
- CSRF / Origin 防护；
- Resume / Job 数据模型与 API；
- Analysis snapshot；
- 异步 Analysis worker；
- OpenAI-compatible 模型调用；
- 结构化匹配结果；
- 原文证据校验；
- 事实边界校验；
- suggestion 生成与安全过滤；
- suggestion decision；
- Draft；
- optimistic version control；
- saved revisions；
- preview；
- Markdown export；
- restore；
- 数据所有权隔离；
- Docker 初始化和验收流程。

---

## 二十、已知限制

1. 当前自动可采纳 suggestion 采用保守的受约束策略。某些人工看来合理的自然语言优化，如果无法严格验证事实安全，会被阻止自动采纳。
2. `needs_confirmation` 表示当前材料证据不足，不代表候选人一定不具备该能力。
3. restore 只恢复历史正文到 working draft，不恢复历史 suggestion decision 状态。
4. 当前只支持文字简历和文字岗位描述。
5. 当前不提供 OCR。
6. 当前不提供 PDF 简历解析。
7. 当前导出格式为 Markdown，不提供 PDF 排版。
8. 当前不提供招聘网站采集。
9. 当前不提供自动投递。
10. 当前不提供面试功能。
11. 当前不提供简历排名或评分排行榜。
12. 当前不提供多模板简历编辑器。
13. 外部模型出现认证失败、限流、超时或服务不可用时，Analysis 可能失败，需要用户显式 retry。
14. 自动修改优先保证事实安全，因此信息缺口较大的场景可能没有可直接采纳的 suggestion，用户仍可依据分析结果手工编辑 Draft。

---

## 二十一、当前最终状态

最终验收状态：

```text
真实前端                PASS
真实 FastAPI 后端       PASS
真实 PostgreSQL         PASS
Docker Compose          PASS
账号认证                PASS
账号数据隔离            PASS
Resume / Job            PASS
真实模型 Analysis       PASS
Analysis snapshot       PASS
证据对应                PASS
事实边界                PASS
Suggestion              PASS
Draft                    PASS
显式保存                PASS
Saved Revision           PASS
Restore                  PASS
Preview                  PASS
Markdown Export          PASS
持久化                   PASS
Backend Tests            59 passed, 3 non-blocking warnings
Frontend Build           PASS
Alembic                  0007_saved_revisions
```

项目已达到当前题目核心功能的最终交付状态。
