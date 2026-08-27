# 保创AI

保险中介 AI 内容智能平台的 PC 端本地演示项目。Stage 01—10 已验收并形成完整本地 MVP：产品资料库、DeepSeek 文字生成、千问图片生成与确定性排版、浏览器动画培训、产品/合规两级审核、批准素材库与规定格式下载、产品版本影响处置、企业品牌中心，以及只读权限矩阵和不可变操作记录均已完成全量自动化与浏览器验收。

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
- `APP_FONT_PATH`：可选的中文字体文件路径；正式图片下载会把批准文案叠加到 PNG，部署环境没有系统中文字体时配置。
- `DASHSCOPE_REGION`：`beijing`、`singapore`、`frankfurt` 或 `tokyo`；Key、Workspace、Endpoint 和模型必须同地域。
- `DASHSCOPE_API_BASE_URL`：可选自定义 API 基地址；为空时根据 Workspace/地域解析，未填 Workspace 时使用仍可用的旧地域域名。
- AI 服务通过后端适配层接入；Key 永远不进入前端、响应、数据库业务载荷或 Git。
- 培训播放使用浏览器 Web Speech；没有中文语音或播放失败时自动降级为静默字幕，不依赖云端 TTS。
- 图片和培训整体提交审核；公众号与小红书按渠道分别提交。产品审核通过后进入合规审核，审核人只决定不可变版本，不直接修改正文。
- 合规批准版本自动进入素材库；图片下载为叠加批准文案的 PNG，文章和培训下载为 DOCX，复制素材只创建独立草稿。
- 已确认产品只能通过新版本更新；新版本保留历史、展示字段差异并设置生效时间，旧版本素材必须经人工保持有效、关联修订、失效或恢复复核。
- 品牌中心支持企业名称、Logo、主/辅色、文风、宣传语、免责声明、1—3 张参考图和 1—3 篇参考文章；新 AI 任务保存当时品牌字段快照。
- 用户与权限页展示 2 个固定演示身份、5 个业务角色和 15 个服务端权限码；本地 MVP 不提供用户、角色或邀请码变更。
- 操作记录支持按操作人、动作、对象、精确对象 ID 和时间筛选；记录只有服务端追加入口，API 无编辑/删除方法，SQLite 触发器同时拒绝更新和删除。
- 独立任务队列、缓存、HTTPS 和日志监控保留到对应后续阶段实施；当前本地演示使用持久化任务表与进程内执行器。

正式多用户登录、邀请码、多企业隔离、火山引擎部署、域名备案与正式发布不属于本地 MVP。启动这些工作前需由项目负责人确认新阶段，并完成相应账号授权或发布确认。
