# 数据字典与来源说明

> 所有数据均为题目提供的**模拟（不涉密）数据**。
> 标注 `[原文]` 的行 100% 来自《霍尼韦尔Hackathon-模拟真题与导师指南》材料 1~4；
> 标注 `[扩展]` 的行是为了让「日志分析类 / 综合判断类」问题可演示、可测试而补充的**合成数据**，
> 删除扩展行不会影响 `[原文]` 部分的正确性。

## 目录

```
data/
├── manuals/                     # 材料 1：设备故障排查手册（3 份纯文本）
│   ├── manual_a_motor_drive.txt
│   ├── manual_b_temperature_sensor.txt
│   └── manual_c_comm_module.txt
├── logs/device_logs.csv         # 材料 2：设备运行日志
├── fault_codes.json             # 材料 3：故障码对照表
└── maintenance_records.json     # 材料 4：维护记录
```

## 1. 手册（材料 1）→ `data/manuals/*.txt`

文件首行是 `【手册X · 设备类型】` 的标题行，其下每条以 `- ` 开头，格式为：

```
- <故障码> <现象>：<排查步骤，逗号分隔>
```

| 文件 | 手册 | 覆盖故障码 | 来源 |
|---|---|---|---|
| `manual_a_motor_drive.txt` | 手册A · 电机驱动器 | E101、E205 | [原文] |
| `manual_b_temperature_sensor.txt` | 手册B · 温度传感器 | T301、T302 | [原文] |
| `manual_c_comm_module.txt` | 手册C · 通信模块 | C404 | [原文] |

向量化入口：`src/hackathon_agent/ingest_manuals.py`。脚本读取各 TXT 的首行作为标题，将后续非空行按 `chunk_size=300`、`chunk_overlap=40` 切分，使用 DashScope OpenAI 兼容 API 的 `qwen3.7-text-embedding` 生成向量，写入 `data/milvus_lite.db` 中的 Milvus Lite `manuals` 集合。当前实现没有 `hackathon_agent.data.loaders` 模块、关键词检索降级或 Chroma 索引。

## 2. 运行日志（材料 2）→ `data/logs/device_logs.csv`

字段（与题目完全一致，**未增删列**）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `timestamp` | `YYYY-MM-DD HH:MM` | 采样时间；当前工作流按该格式的文本时间顺序排序 |
| `device_id` | str | 设备编号，如 `DEV-001` |
| `temperature` | float | 温度（℃） |
| `vibration` | float | 振动（mm/s） |
| `rpm` | float | 转速（r/min），通信类设备为 0 |
| `status` | `normal` \| `alert` | 设备状态 |

行来源说明：

| 行 | 设备 | 来源 |
|---|---|---|
| 08:00–12:00 共 5 行 | DEV-001 | **[原文]** 与题目材料 2 逐字节一致 |
| 13:00–15:00 共 3 行 | DEV-001 | [扩展] 补「告警后回落」用于趋势分析 |
| 08:00–12:00 共 5 行 | DEV-002 | [扩展] 温度传感器，模拟 T301 温度过高 |
| 08:00–11:00 共 4 行 | DEV-003 | [扩展] 通信模块，模拟 C404 通信中断（仅 status=alert，无温振异常） |

> DEV-002 / DEV-003 的两条告警分支，用于验证 Agent 能否区分
> 「温振指标异常」和「只有状态告警、指标正常」两种情况。

## 3. 故障码对照表（材料 3）→ `data/fault_codes.json`

```json
{"code": "E101", "meaning": "过流报警", "severity": "高", "suggest": "立即停机检查电机与接线"}
```

4 条记录（E101 / E205 / T301 / C404）**与题目逐字一致 [原文]**。
注意：**T302 在手册 B 中有、但故障码表中没有**，这是数据层面的「部分命中」case。
当前 Agent 尚未查询此 JSON，因此不能保证它会检查并指出故障码表中缺少 T302。

## 4. 维护记录（材料 4）→ `data/maintenance_records.json`

字段：`device_id` / `last_maintenance`（`YYYY-MM-DD`）/ `type`。

| 记录 | 来源 |
|---|---|
| DEV-001 · 2023-12-01 · 常规保养 | [原文] |
| DEV-002 · 2024-03-15 · 传感器校准 | [扩展] |
| DEV-003 · 2024-05-20 · 固件升级 | [扩展] |

`DEV-999` 故意**不存在**，用于验证「未找到」分支。

## 5. 数据 → 工具映射（需求结构化）

| 问题类型 | 需要的数据 | 对应工具 |
|---|---|---|
| 故障排查 | 手册向量库 | `search_manuals`（Embedding + Milvus Lite 检索） |
| 日志分析 | 运行日志 CSV + 维护记录 JSON | `analyze_device_logs`（LangGraph 工作流；程序确定性聚合，LLM 解释） |

`fault_codes.json` 是项目中的参考数据，但当前 Agent 尚未提供读取它的独立工具。日志分析工作流仅将 `status=alert` 计为明确告警；数据中没有温度/振动告警阈值。工作流以文件中的最新采样时间为准，不能用于实时监控。
