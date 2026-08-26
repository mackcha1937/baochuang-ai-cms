# 保创AI

保险中介 AI 内容智能平台的 PC 端本地演示项目。Stage 01—03 已验收；当前位于 **Stage 04：图片生成与确定性排版**，已完成千问异步视觉任务、程序文字排版、结构化图片编辑、失败恢复、安全存储和自动化/浏览器无 Key 验收，等待项目负责人配置真实千问凭据后完成供应商联调。

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

真实密钥只能由项目负责人本人写入本地 `.env`，不得提交或分享。DeepSeek 使用 `DEEPSEEK_API_KEY`；千问图片使用 `DASHSCOPE_API_KEY`。推荐同时填写与 Key 同地域的 `DASHSCOPE_WORKSPACE_ID` 和 `DASHSCOPE_REGION`，模型默认 `qwen-image-3.0`。

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

完整的阶段检查清单、失败修复和验收结论记录在 `reports/qa/stage-NN.md`。

## 配置与部署替换点

- `APP_DATABASE_URL`：当前为 SQLite，后续可由数据访问层替换 PostgreSQL。
- `APP_UPLOAD_DIR`：产品源文件本地目录；`APP_GENERATED_DIR`：视觉素材本地目录，后续统一替换火山引擎 TOS。
- `APP_MAX_VISUAL_BYTES`：视觉素材下载/上传安全上限，默认 15 MB。
- `DASHSCOPE_REGION`：`beijing`、`singapore`、`frankfurt` 或 `tokyo`；Key、Workspace、Endpoint 和模型必须同地域。
- `DASHSCOPE_API_BASE_URL`：可选自定义 API 基地址；为空时根据 Workspace/地域解析，未填 Workspace 时使用仍可用的旧地域域名。
- AI 服务通过后端适配层接入；Key 永远不进入前端、响应、数据库业务载荷或 Git。
- 独立任务队列、缓存、HTTPS 和日志监控保留到对应后续阶段实施；当前本地演示使用持久化任务表与进程内执行器。
