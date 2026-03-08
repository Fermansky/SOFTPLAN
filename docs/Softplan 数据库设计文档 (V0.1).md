# Softplan 数据库设计文档 (V0.1)

本架构设计旨在支持“分析过程可视化、人工介入修正、多版本对比”的产品核心需求。

## 1. 实体关系图 (ERD)

```
erDiagram
    PROJECT ||--o{ DOCUMENT : "包含"
    PROJECT ||--o{ ANALYSIS_VERSION : "拥有版本"
    DOCUMENT ||--o{ ANALYSIS_VERSION : "作为输入"
    
    ANALYSIS_VERSION ||--o{ REQUIREMENT_ITEM : "定义需求"
    ANALYSIS_VERSION ||--o{ DATA_ENTITY : "识别实体"
    ANALYSIS_VERSION ||--o{ FUNCTION_POINT : "分析功能点"
    ANALYSIS_VERSION ||--o{ ESTIMATION_RESULT : "产出估算"
    
    FUNCTION_POINT ||--o{ MODIFICATION_LOG : "记录修改"
    ANALYSIS_VERSION ||--o{ LLM_TRACE : "记录推理过程"
```

## 2. 核心表结构

### 2.1 项目表 (projects)

管理估算任务的基本单元。

| **字段名**         | **类型**  | **描述**        | **说明**                              |
| ------------------ | --------- | --------------- | ------------------------------------- |
| id                 | UUID      | 主键            |                                       |
| name               | String    | 项目名称        |                                       |
| description        | Text      | 项目描述        |                                       |
| status             | Enum      | 项目状态        | draft, analyzing, completed, archived |
| current_version_id | UUID      | 当前活动版本 ID | 关联到最新的有效估算版本              |
| created_at         | Timestamp | 创建时间        |                                       |
| updated_at         | Timestamp | 更新时间        |                                       |
| deleted_at         | Timestamp | 逻辑删除时间    | `NULL` 表示未删除，非空表示已删除         |

### 2.2 文档表 (documents)

存储原始文档信息及其解析后的结构化文本。

| **字段名**     | **类型**  | **描述**       | **说明**                           |
| -------------- | --------- | -------------- | ---------------------------------- |
| id             | UUID      | 主键           |                                    |
| project_id     | UUID      | 外键           | 关联项目                           |
| file_name      | String    | 文件名         |                                    |
| file_path      | String    | 存储路径       | 云存储或本地路径                   |
| file_type      | String    | 扩展名         | 如 pdf, docx                       |
| raw_content    | LongText  | 解析后的纯文本 | 转换后的全量结构化文本             |
| structure_json | JSON      | 章节结构信息   | 存储标题、段落等元数据             |
| status         | Enum      | 解析状态       | pending, processing, success, fail |
| created_at     | Timestamp | 上传时间       |                                    |

### 2.3 分析版本表 (analysis_versions)

每次重算或关键修正生成的快照。

| **字段名**        | **类型**  | **描述**  | **说明**                       |
| ----------------- | --------- | --------- | ------------------------------ |
| id                | UUID      | 主键      |                                |
| project_id        | UUID      | 外键      |                                |
| doc_id            | UUID      | 外键      | 本次分析基于的文档版本         |
| version_num       | Integer   | 版本序号  | 如 1, 2, 3...                  |
| tag               | String    | 备注/标签 | 用户填写的版本说明             |
| parent_version_id | UUID      | 父版本 ID | 用于追踪是从哪个版本修改而来的 |
| created_at        | Timestamp | 创建时间  |                                |

### 2.4 需求项表 (requirement_items)

LLM 自动提取的需求/功能模块清单。

| **字段名**    | **类型** | **描述**  | **说明**               |
| ------------- | -------- | --------- | ---------------------- |
| id            | UUID     | 主键      |                        |
| version_id    | UUID     | 外键      | 关联版本               |
| module_name   | String   | 所属模块  |                        |
| title         | String   | 需求标题  |                        |
| description   | Text     | 需求详述  |                        |
| source_ref    | String   | 文档依据  | 文档中的页码或段落索引 |
| ai_confidence | Float    | AI 置信度 | 0-1 之间               |

### 2.5 功能点分析表 (function_points)

IFPUG 功能点分析核心表。

| **字段名**    | **类型** | **描述**       | **说明**               |
| ------------- | -------- | -------------- | ---------------------- |
| id            | UUID     | 主键           |                        |
| version_id    | UUID     | 外键           |                        |
| req_item_id   | UUID     | 外键           | 关联需求项             |
| fp_name       | String   | 功能点名称     |                        |
| fp_type       | Enum     | 功能类型       | EI, EO, EQ, ILF, EIF   |
| complexity    | Enum     | 复杂度         | Low, Average, High     |
| det_count     | Integer  | DET 数量       | 字段个数               |
| ret_ftr_count | Integer  | RET/FTR 数量   | 逻辑文件或引用文件个数 |
| unadjusted_fp | Float    | 未调整功能点数 | 计算得出的点数         |
| logic_desc    | Text     | 计算逻辑说明   | AI 生成的判定理由      |
| is_modified   | Boolean  | 是否被人工修改 |                        |
| user_notes    | Text     | 人工备注       | 修改理由               |

### 2.6 估算结果表 (estimation_results)

存储最终的工作量和成本结论。

| **字段名**           | **类型** | **描述**       | **说明**                        |
| -------------------- | -------- | -------------- | ------------------------------- |
| id                   | UUID     | 主键           |                                 |
| version_id           | UUID     | 外键           |                                 |
| total_fp             | Float    | 总功能点数     |                                 |
| effort_person_months | Float    | 工作量(人月)   |                                 |
| total_cost           | Decimal  | 总成本(元)     |                                 |
| model_name           | String   | 使用的成本模型 | 如 "Simple-Linear", "COCOMO-II" |
| model_params         | JSON     | 模型参数快照   | 记录当时的费率、因子等          |
| result_json          | JSON     | 详细结果报表   | 结构化的导出数据                |

### 2.7 LLM 推理记录表 (llm_traces)

存储“高可解释性”所需的推理过程。

| **字段名**  | **类型** | **描述**       | **说明**                          |
| ----------- | -------- | -------------- | --------------------------------- |
| id          | UUID     | 主键           |                                   |
| version_id  | UUID     | 外键           |                                   |
| stage       | String   | 阶段           | parsing, requirement, ifpug, cost |
| prompt      | Text     | 输入提示词     |                                   |
| response    | Text     | AI 输出原文    |                                   |
| model_id    | String   | 使用的模型版本 | 如 gpt-4o, gemini-1.5-pro         |
| token_usage | Integer  | Token 消耗     |                                   |

### 2.8 修改日志表 (modification_logs)

记录人工对分析结果的干预轨迹。

| **字段名**   | **类型**  | **描述**    | **说明**           |
| ------------ | --------- | ----------- | ------------------ |
| id           | UUID      | 主键        |                    |
| version_id   | UUID      | 外键        |                    |
| target_table | String    | 目标表名    | 如 function_points |
| target_id    | UUID      | 目标数据 ID |                    |
| field_name   | String    | 修改字段    |                    |
| old_value    | Text      | 修改前值    |                    |
| new_value    | Text      | 修改后值    |                    |
| reason       | Text      | 修改原因    |                    |
| created_at   | Timestamp | 修改时间    |                    |

## 3. 设计要点说明

1. **版本隔离**：通过 `ANALYSIS_VERSION` 表，系统可以轻松实现“对比两个版本的估算差异”。用户修改数据时，系统可以创建一个新版本，也可以在当前草稿版本上直接修改。
2. **溯源性**：`requirement_items` 的 `source_ref` 和 `function_points` 的 `logic_desc` 共同支撑了 PRD 要求的“高可解释性”，让用户知道每一个功能点是怎么算出来的。
3. **松耦合**：`estimation_results` 中的 `model_params` 和 `model_name` 允许未来接入不同的成本估算方法，而无需大幅改动表结构。
4. **可干预性**：`is_modified` 标记能帮助用户在界面上快速区分哪些是 AI 自动生成的，哪些是经过人工确认的。
5. **逻辑删除**：`projects` 表通过 `deleted_at` 实现逻辑删除，项目删除时仅写入删除时间；列表、详情、更新等查询默认仅返回 `deleted_at IS NULL` 的记录。
