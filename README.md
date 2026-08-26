# 保创AI

保险中介 AI 内容智能平台的 PC 端本地演示项目。当前代码完成到 **Stage 01：项目基础骨架**，提供 React/Vite/TypeScript 应用壳、FastAPI/SQLite 基础服务、10 个一级导航入口以及预置达尔文12号产品数据。

## 环境要求

- Python 3.11+
- Node.js 22+
- npm 10+

## 首次安装

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
npm install
```

真实密钥只能由项目负责人本人写入本地 `.env`。Stage 01 不需要任何 API Key，也不会发起外部 AI 请求。

## 启动

生产构建式本地演示（单端口，推荐验收使用）：

```bash
npm run demo
```

访问 `http://127.0.0.1:8765`。按 `Control-C` 停止。

前后端热更新开发：

```bash
npm run dev
```

开发前端访问 `http://127.0.0.1:5173`，API 仍在 `http://127.0.0.1:8765`。

## 自检

```bash
npm run check
```

该命令依次执行前端单元测试、后端/API 测试、TypeScript 编译和生产构建。启动本地演示后再执行：

```bash
bash tests/smoke.sh
```

完整的阶段检查清单、失败修复和验收结论记录在 `reports/qa/stage-01.md`。

## 配置与部署替换点

- `APP_DATABASE_URL`：当前为 SQLite，后续可由数据访问层替换 PostgreSQL。
- `data/uploads/`：当前本地目录，后续替换火山引擎 TOS。
- AI 服务：后续通过后端适配层接入；Key 永远不进入前端。
- 后台任务、缓存、HTTPS、日志监控：均保留到对应后续阶段实施，不在 Stage 01 越界实现。
