# Hackathon Agent

基于 LangChain `create_agent` 和 LangGraph 构建的中文设备维护助手。它提供两类能力：

- **设备故障排查**：由 Agent 调用 `search_manuals`，用 Qwen Embedding 将问题向量化，在本地 Milvus Lite 手册库中检索相关内容，再由聊天模型组织答案。
- **设备运行日志分析**：由 Agent 调用 `analyze_device_logs` 工具。该工具封装一个显式 LangGraph 工作流，读取设备日志与维护记录，先由程序计算统计值，再由聊天模型解释趋势。

Agent 的工具选择由模型根据用户问题和系统提示完成。日志分析工具不会自动用于所有问题；若模型未选择该工具，Agent 不会强制运行它。

## 架构与数据流

```text
用户问题
   |
   v
LangChain create_agent（底层由 LangGraph 执行工具调用循环）
   |-- 故障排查 --> search_manuals --> Qwen Embedding --> Milvus Lite
   |                                                --> 检索片段 --> 聊天模型回答
   |
   `-- 日志分析 --> analyze_device_logs（LangGraph 子工作流）
                     --> 提取设备编号
                     --> 读取 CSV 日志与 JSON 维护记录
                     --> 程序统计温振趋势和告警
                     --> 聊天模型生成分析说明
```

### 手册问答

`data/manuals/*.txt` 中每个手册以标题行开头，之后是故障码、现象与排查步骤。运行导入脚本后，手册内容会按条目切分并存入本地 Milvus Lite 的 `manuals` 集合。检索工具返回最多 4 条相关片段及其来源，Agent 根据系统提示使用检索结果回答。

故障码对照数据位于 `data/fault_codes.json`，目前 Agent 运行时提供的工具是手册向量检索 `search_manuals`；该 JSON 文件尚未接入独立的故障码查询工具。

### 日志分析

`analyze_device_logs` 调用 [log_workflow.py](src/hackathon_agent/log_workflow.py) 中的 LangGraph 子工作流：

1. 从问题文本中提取形如 `DEV-001` 的设备编号；缺少编号或包含多个编号时返回提示。
2. 从 `data/logs/device_logs.csv` 读取该设备的记录，并按时间排序；同时从 `data/maintenance_records.json` 查找维护记录。
3. 由 Python 确定性计算采样数、时间范围、温度和振动的首末值/变化量/最小值/最大值及峰值时间、告警记录数和连续告警时段数。
4. 将原始记录和统计结果交给聊天模型生成中文说明；模型输出为空时会明确报错。

`status=alert` 是当前数据中唯一明确的告警标记。项目没有配置温度或振动阈值，因此不会仅凭数值判断超标或故障。日志是静态历史样例，不代表实时状态；回答应以数据中最新的采样时间为准。

## 环境要求

- Python 3.12（见 `.python-version`）
- [uv](https://docs.astral.sh/uv/)
- DashScope API Key（用于 Qwen 聊天和 Embedding 模型），或兼容 OpenAI API 的服务
- 可选：LangSmith API Key（用于追踪及 Studio 调试）

在项目目录（即包含 `pyproject.toml` 的 `hackathon-agent/`）创建 `.env`：

```env
DASHSCOPE_API_KEY=你的_API_Key

# 可选：不设置时使用 DashScope 北京地域 OpenAI 兼容接口
# DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# 可选：启用 LangSmith tracing 时填写
# LANGSMITH_TRACING=true
# LANGSMITH_API_KEY=你的_LangSmith_API_Key
# LANGSMITH_PROJECT=hackathon-agent
```

也可以使用 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL` 作为兼容服务配置。不要提交 `.env`，也不要将 API Key 放入问题或测试数据。LangSmith trace 可能记录用户输入、模型输出及检索到的手册内容。

## 安装与首次准备

在项目根目录执行：

```bash
uv sync
```

可选：检查本地 Milvus Lite 是否能创建集合、写入并检索：

```bash
uv run python -m hackathon_agent.check_milvus
```

检查会在 `data/milvus_lite.db` 中创建临时集合、完成测试后删除该集合；数据库文件会保留。

首次使用手册检索前，生成向量并建立 Milvus 集合：

```bash
uv run python -m hackathon_agent.ingest_manuals
```

该命令使用 `qwen3.7-text-embedding`，将手册切分（`chunk_size=300`、`chunk_overlap=40`）后写入 `data/milvus_lite.db` 的 `manuals` 集合。**重复运行会删除并重建 `manuals` 集合**；数据库中的其他集合不会被该脚本主动删除。

## 运行

```bash
uv run python -m hackathon_agent.agent
```

输入问题后交互；输入 `/quit` 或 `/exit` 退出。示例：

```text
E101 过流报警应该如何排查？
设备DEV-001最近有没有异常？
```

Agent 使用 `qwen3.8-max` 作为聊天模型。日志分析问题会读取 CSV/JSON 本地数据，无需将日志导入向量库。

## 在 LangSmith 中追踪与测试

启用 tracing 时，在 `.env` 中设置：

```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=你的_LangSmith_API_Key
LANGSMITH_PROJECT=hackathon-agent
```

重启 Agent 并提问后，可在 LangSmith 的对应项目查看模型与工具调用。追踪包含敏感内容的风险请见上文提示。

### 使用 LangGraph Studio

项目配置了 LangGraph Studio 本地开发入口：[langgraph.json](langgraph.json) 指向 [studio.py](src/hackathon_agent/studio.py) 中的 `graph`。安装开发依赖并启动：

```bash
uv sync --dev
uv run langgraph dev
```

打开命令输出中的 Studio 地址（通常是 `http://localhost:2024`），选择 `maintenance_agent`：

- 输入设备故障问题，观察 `search_manuals` 和手册检索过程。
- 输入 `设备DEV-001最近有没有异常？`，观察 `analyze_device_logs` 及日志分析子工作流。
- 输入 `最近有没有异常？`，检查缺少设备编号时的处理。
- 输入 `DEV-999最近有没有异常？`，检查无匹配日志时的处理。

若同时运行命令行 Agent 和 Studio，两者可能同时访问本地 Milvus Lite 数据库；调试时建议只保留一个进程，避免数据库占用冲突。Studio 配置用于本地开发测试，不表示已部署到 LangSmith Cloud。

## 测试

日志工作流单元测试使用模拟聊天模型，不需要调用外部模型 API：

```bash
uv run python -m unittest discover -s tests -v
```

测试覆盖真实样例数据统计、设备编号缺失/多编号、无匹配日志，以及工具对工作流的调用。

## 项目结构

```text
hackathon-agent/
├── data/
│   ├── manuals/                 # 设备故障排查手册
│   ├── logs/device_logs.csv     # 设备运行日志
│   ├── fault_codes.json         # 故障码对照数据（目前未接为独立工具）
│   └── maintenance_records.json # 设备维护记录
├── src/hackathon_agent/
│   ├── agent.py                 # Agent、手册检索工具及命令行入口
│   ├── log_workflow.py          # 日志分析 LangGraph 工作流及其 Tool 包装
│   ├── ingest_manuals.py        # 手册向量化并写入 Milvus Lite
│   ├── check_milvus.py          # Milvus Lite 本地连通性自检
│   └── studio.py                # LangGraph Studio 图入口
├── tests/
│   └── test_log_workflow.py     # 日志工作流单元测试
├── langgraph.json               # Studio 本地图配置
├── pyproject.toml               # 项目依赖与构建配置
└── uv.lock                      # 锁定的依赖版本
```

数据字段、样例来源和数据扩展说明见 [data/README.md](data/README.md)。
