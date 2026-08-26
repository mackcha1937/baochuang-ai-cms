# Stage 00：开发准备验收记录

- 状态：通过
- 日期：2026-08-26
- 范围：Git基线、分支规范、VS Code配置、密钥隔离和火山引擎上线预留
- 不包含：任何业务功能新增或修改

## 验收项

- [x] Git仓库以 `main` 为稳定基线
- [x] `stage/00-preparation` 分支保存本阶段成果
- [x] `.env`、运行数据、上传文件、临时输出不会被提交
- [x] VS Code应用可打开项目，调试与任务配置为合法JSON
- [x] Python现有代码通过语法检查
- [x] 项目规则明确禁止未验收进入下一阶段
- [x] 真实API Key操作明确交由项目负责人
- [x] 火山引擎上线替换点已记录

## 执行结果

- `python3 -m py_compile server.py`：通过；缓存定向至项目内 `.cache/pyc`。
- VS Code `settings.json`、`launch.json`、`tasks.json`、`extensions.json`：JSON解析通过。
- Git忽略检查：`.env`、`data/state.json`、上传内容、`work`、`outputs`、`.cache` 均命中规则。
- 密钥扫描：未发现疑似真实DeepSeek、阿里云或通用 `sk-` 密钥。
- 启动测试：服务成功监听 `127.0.0.1:8876`，测试后已正常关闭。
- `GET /api/health`：HTTP 200，返回 `ok: true`。
- `GET /api/config`：HTTP 200，仅返回是否配置和模型名，不返回密钥。
- `GET /api/state`：HTTP 200，返回合法初始状态和预置产品。

## 失败与修复记录

第一次语法检查因macOS系统Python尝试写入受限缓存目录失败。已在VS Code运行与测试配置中将 `PYTHONPYCACHEPREFIX` 指向项目内忽略目录，随后重新执行全部检查并通过。

## 结论

Stage 00 全量自检通过，可以保存本阶段分支；在收到下一阶段明确需求前不创建后续分支、不新增业务功能。
