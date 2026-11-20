# Readialog · 阅谈

你喜欢什么样的故事？Readialog 用多轮访谈了解你的阅读口味、叙事偏好和阅读习惯，最后生成可保存的个人阅读画像。这是一个在 Codex 协助下独立重写的 Python / LangGraph 项目。

[查看示例画像](examples/sample-profile.md)

- **访谈**根据上一轮回答继续追问，最多 12 轮。你也可以提前结束，用已经提供的回答生成画像。
- **画像**包含喜欢的书、风格评分和依据、理想故事与阅读习惯。输出包括 JSON、Markdown、分享文本和对话日志。
- **会话恢复**可选用 Redis，支持跨进程继续访谈和导入原版 ReadWren 的旧会话。

## 在 Codex 里使用

将仓库的 GitHub 链接交给 Codex，请它把完整项目克隆到本机。以克隆后的目录新开一个本地 Codex 对话，然后输入：

```text
$readialog 用中文采访我的阅读偏好，一次问我一个问题。
```

Codex 会读取仓库自带的 [Readialog 技能](.agents/skills/readialog/SKILL.md)，在对话里提问和生成画像。这个模式使用读者自己的 Codex 登录和额度。导出文件前按下面的步骤装好 Python 依赖即可，无需配置 DeepSeek 或 Kimi Key。 [Codex 项目技能说明](https://learn.chatgpt.com/docs/build-skills)

## 快速开始

下载或克隆仓库后，在仓库根目录用 Python 3.11 或更新版本安装依赖。

```powershell
python -m venv .venv
& ./.venv/Scripts/python.exe -m pip install -r requirements.txt
```

在 macOS 或 Linux，创建虚拟环境后用 `.venv/bin/python -m pip install -r requirements.txt` 安装。下文的 Windows 命令都可以把解释器替换成 `.venv/bin/python`。

### 无需 Key 的离线演示

演示使用虚构回答和固定模型响应，走完实际 12 轮访谈和导出流程。命令结束时会显示输出文件的路径。

```powershell
& ./.venv/Scripts/python.exe examples/run_demo.py
```

### 真实访谈

在 Windows 安装好依赖后，双击 [`启动阅谈.cmd`](启动阅谈.cmd)。粘贴 DeepSeek API Key 并按回车，就会看到第一道中文问题。输入时会隐藏 Key，程序只在本次运行时使用它。回答一两题后可以输入 `quit` 生成简版画像并查看保存位置。

如果 DeepSeek 返回 `401`，窗口会再次询问 Key。粘贴有效的 Key 后会重试刚才的操作，直接回车则退出。可以在 [DeepSeek 开放平台](https://platform.deepseek.com/api_keys) 创建 Key。

如果希望通过配置文件启动，可以将 `env.example` 复制为 `.env` 并填写 `DEEPSEEK_API_KEY`。

```powershell
Copy-Item -LiteralPath env.example -Destination .env
notepad .env
& ./.venv/Scripts/python.exe cli_interview.py
```

追问和生成画像默认使用 `deepseek-flash`，画像调用启用 JSON 输出。可分别通过 `DEEPSEEK_INTERVIEW_MODEL`、`DEEPSEEK_PROFILE_MODEL` 更换模型，通过 `DEEPSEEK_THINKING=enabled` 启用思考模式。输出上限由对应的 `DEEPSEEK_*_MAX_TOKENS` 设置。 [DeepSeek API 接入](https://api-docs.deepseek.com/en/) · [JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)

使用 Kimi 时，在 `.env` 设 `READWREN_PROVIDER=moonshot` 并填写 `MOONSHOT_API_KEY`。两阶段分别使用 `MOONSHOT_INTERVIEW_MODEL` 和 `MOONSHOT_PROFILE_MODEL`。 [Kimi API 说明](https://platform.kimi.com/docs/guide/use-thinking-models)

## 访谈和恢复

- 回答第 12 题后生成画像。输入 `quit`、`q` 或 `exit` 会根据已有回答提前生成。终止输入时也会尝试用已保存回答生成。
- 档案、对话日志、Markdown 和分享文本保存在 `user_profiles/<会话ID>/`。模型 API 如果返回 `reasoning_content`，日志和原始 JSON 可以保留该字段。启动时可选择是否在屏幕显示。
- 将 `REDIS_HOST`、`REDIS_PORT` 和可选的 `REDIS_PASSWORD` 填入 `.env`，启用跨进程恢复。默认有效期为 24 小时。Redis 未配置或启动时连接失败，当前进程使用内存。
- 画像生成失败时，第 12 个回答仍在已提交的会话中。恢复同一 Redis 会话可重试生成。若画像已完成，恢复只重新导出已有结果。

```powershell
& ./.venv/Scripts/python.exe cli_interview.py --resume SESSION_ID
& ./.venv/Scripts/python.exe scripts/view_redis_sessions.py
& ./.venv/Scripts/python.exe scripts/view_session_conversation.py SESSION_ID
& ./.venv/Scripts/python.exe scripts/retrieve_profile.py SESSION_ID
& ./.venv/Scripts/python.exe scripts/view_conversation_log.py PATH_TO_CONVERSATION_JSON
```

使用 `retrieve_profile.py --log PATH_TO_CONVERSATION_JSON` 可以从保存的日志重新调用模型生成档案。正常读取 Redis 存档和查看日志均可离线进行。用 `--reasoning-file PATH` 可把 API 提供的推理字段单独导出。

### 导入原版 Redis 会话

把 `.env` 的 Redis 配置指向存有原版会话的服务，然后导入会话。导入完成后，从新版命令行继续。

```powershell
& ./.venv/Scripts/python.exe scripts/import_legacy_session.py SESSION_ID
& ./.venv/Scripts/python.exe cli_interview.py --resume SESSION_ID
```

原版命令行可能把最终画像只保存在本地。若已有对应的 `profile_*.json`，导入时加 `--profile PATH_TO_PROFILE_JSON`。新版会保留已有画像，恢复时直接显示和导出。只有旧会话的全部回答且没有画像时，下一次恢复会重新生成。

导入只读原版检查点，将新版会话写入另一组 Redis 键，并沿用导入时剩余的有效期。重复导入会报错。导入会去除原版重复保存的同一条消息，同时保留用户在不同轮次说过的相同内容。

## 实现与验收

访谈图用 LangGraph 路由到追问或画像生成。追问节点根据答案长度、表达和已覆盖话题调整提示。画像节点采用独立的模型配置与可编辑评分说明。程序调用可使用 `from wren import InterviewAgent, ProfileGeneratorAgent, ProfileSaver`。

| 行为 | 验收方式 |
|---|---|
| 从第 1 题到第 12 题、提前退出、输入中断 | 运行原版 CLI 的固定模型替身作为基线，再运行本项目实际命令行。核对终态、轮数、日志和三种画像文件。 |
| 追问与画像请求 | 本地 HTTP 替身核对 DeepSeek 和 Kimi 的模型名、请求路径、鉴权头及消息顺序，并核对 DeepSeek 的 JSON 输出配置。 |
| 跨进程恢复、查会话、看日志、取画像 | 通过本地 Redis 协议夹具，用不同进程执行正常入口。核对 TTL、会话状态和写入冲突。 |
| 原版存档与画像 | 用原版实际程序生成的检查点验证导入、重复记录清理，以及附带已有画像的完整访谈。并用同一份画像核对 Markdown 中的阅读理念与百分比。 |
| 空密钥、过长输入、模型失败及 JSON 异常 | 核对空密钥提前退出、输入截断、失败状态、末轮不自动重发和按会话恢复。 |

验证使用本机 HTTP 和 Redis 协议夹具。真实 DeepSeek、Kimi 和 Redis 服务尚未验收。

```powershell
& ./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
& ./.venv/Scripts/python.exe -m unittest discover -s tests -v
```

## 开发说明

阅谈以 ReadWren 的公开功能和运行行为为参考，增加了 DeepSeek 接入、旧 Redis 会话迁移、离线演示和 Codex 对话入口。命令行版的访谈状态按实际回答计数，每轮只发送一次已保存的历史。 [参考项目与基准提交](https://github.com/muratcankoylan/readwren/tree/3d0bfe481a340f247c749c082b7a65c877c12de1)
