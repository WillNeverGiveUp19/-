## Milvus Lite 本地向量数据库

项目已配置 `pymilvus[milvus-lite]` 和 `langchain-milvus`。同步依赖并运行连通性自检：

```bash
uv sync
uv run python -m hackathon_agent.check_milvus
```

自检会在 `data/milvus_lite.db` 创建本地数据库，测试插入和向量检索，并删除本次创建的临时集合。数据库文件会保留；已有其他集合不会被改动。

### 手册向量化

在项目目录运行以下命令，会按手册条目切分 `data/manuals/*.txt`，使用 DashScope OpenAI 兼容 API 调用 `qwen3.7-text-embedding`，并将向量存入同一个数据库中的 `manuals` 集合：

```bash
uv run python -m hackathon_agent.ingest_manuals
```

`.env` 需要配置 `DASHSCOPE_API_KEY`；可选配置 `DASHSCOPE_BASE_URL`（未配置时使用北京地域兼容接口）。重复运行会重建 `manuals` 集合以避免重复数据，不影响数据库中的其他集合。

### 运行设备维护 Agent

先完成手册向量化，再启动使用 `qwen3.8-max` 的 LangChain Agent。Agent 会在回答设备维护问题时调用 `search_manuals` 工具，从 Milvus Lite 检索相关手册内容：

```bash
uv run python -m hackathon_agent.agent
```

输入 `/quit` 或 `/exit` 退出。也可以在 Python 代码中调用 `create_manual_agent()` 并使用 LangChain 的 `invoke()` 接口。

### 在 LangSmith 中调试

LangChain 会自动追踪 Agent、模型和工具调用。在项目根目录的 `.env` 追加以下配置，并填入 LangSmith 控制台生成的 API Key：

```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=你的_LangSmith_API_Key
LANGSMITH_PROJECT=hackathon-agent
```

重启 Agent 并发起一次提问后，在 LangSmith 的 `hackathon-agent` 项目中查看 trace；`agent`、`search_manuals`、模型调用及检索结果会组成同一次运行记录。启用 tracing 但没有设置 `LANGSMITH_API_KEY` 时，Agent 会给出明确配置错误。

注意：trace 可能包含用户提问、模型输入输出和检索到的手册内容。请勿在问题或手册中放入密钥、个人信息或其他不应上传的数据。

### 在 LangSmith Studio 中测试

项目已提供 LangGraph Studio 本地开发配置。确认项目根目录 `.env` 中有模型和 LangSmith 配置（包括 `DASHSCOPE_API_KEY`、`LANGSMITH_TRACING=true`、`LANGSMITH_API_KEY`），然后在项目根目录启动开发服务器：

```bash
uv sync --dev
uv run langgraph dev
```

服务器启动后，在终端输出的本地 Studio URL 打开 Studio；通常可访问 `http://localhost:2024`。在 Studio 中选择 `maintenance_agent`，发送设备故障问题即可观察模型、`search_manuals` 工具和 Milvus 检索结果。关闭已运行的 `python -m hackathon_agent.agent` 进程，避免两个进程同时连接本地 Milvus Lite 数据库。

图入口定义在 `src/hackathon_agent/studio.py`，Studio 配置在 `langgraph.json`。此配置用于本机开发测试，不代表已部署到 LangSmith Cloud。