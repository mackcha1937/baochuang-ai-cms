# Codex开发交接

## 当前状态

- 项目根目录：`/Users/zzymima0000/Desktop/个人项目-cms`
- 当前分支：`stage/01-foundation`
- Stage 00：已验收并保留在`stage/00-preparation`
- Stage 01：代码与全量自检已通过，验收证据见`reports/qa/stage-01.md`
- 下一步：提交Stage 01；等待用户确认后再创建Stage 02分支

## Stage 01已交付

- React + Vite + TypeScript PC端应用壳
- 10个一级导航和12个可访问路由
- FastAPI + SQLite基础服务和单端口演示启动
- 超级演示用户、品牌、角色权限、统计和操作记录
- 预置达尔文12号产品列表、搜索、详情、空状态和404
- 本地持久化、加载/异常/重试状态、安全边界与自动化测试

## 未进入的后续范围

- 文件上传与PDF/DOCX/XLSX解析
- DeepSeek文字生成与千问图片生成
- 图片、文章、动画培训的完整向导
- 两级审核状态机、素材下载与版本更新影响
- 邀请码、多用户登录和火山引擎部署

## 强制规则

继续遵守根目录`AGENTS.md`和`docs/DEVELOPMENT_WORKFLOW.md`：当前阶段全量自检未通过不得进入下一阶段；阶段分支必须保留；真实API Key只能由用户本人填写，不得进入Git、前端或日志。
