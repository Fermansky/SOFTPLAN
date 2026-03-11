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

### 2.2 软件主表 (softwares)

存储软件资产的身份信息，作为跨项目引用软件的唯一基准。

| **字段名**      | **类型**    | **描述** | **说明**                               |
| --------------- | ----------- | -------- | -------------------------------------- |
| **id**          | UUID        | 主键     | 默认使用 `gen_random_uuid()`           |
| **code**        | TEXT        | 软件编号 | 唯一索引，用于跨项目识别（如：SW-001） |
| **name**        | TEXT        | 软件名称 | 软件的通用展示名称                     |
| **description** | TEXT        | 软件描述 | 记录软件的功能定位或背景信息           |
| **created_at**  | TIMESTAMPTZ | 创建时间 | 软件资产首次录入系统的时间             |
| **updated_at**  | TIMESTAMPTZ | 更新时间     | 最后一次信息修改时间           |
| **deleted_at**  | TIMESTAMPTZ | 逻辑删除时间 | 非空表示该软件资产已下线/废弃  |

------

### 2.3 项目软件关联表 (project_software_relation)

管理项目与软件的多对多关系，记录特定项目背景下的软件版本状态。

| **字段名**      | **类型**    | **描述**      | **说明**                                   |
| --------------- | ----------- | ------------- | ------------------------------------------ |
| **project_id**  | UUID        | 复合主键/外键 | 关联 `projects.id`，级联删除               |
| **software_id** | UUID        | 复合主键/外键 | 关联 `softwares.id`，级联删除              |
| **version**     | TEXT        | 项目内版本号  | 记录本项目涉及的具体软件版本（如：v2.1.0） |
| **created_at**  | TIMESTAMPTZ | 关联时间      | 软件被分配到项目的时间                     |

------

### 2.4 物理文件表 (files)

存储文件的物理属性及 MinIO 存储映射。该表与业务逻辑无关，主要用于实现文件去重（秒传）和物理存储管理。

| **字段名**         | **类型**    | **描述**  | **说明**                                                     |
| ------------------ | ----------- | --------- | ------------------------------------------------------------ |
| **id**             | UUID        | 主键      | 默认使用 `gen_random_uuid()`，物理文件的唯一标识。           |
| **file_hash**      | TEXT        | 文件哈希  | **唯一索引**。存储文件的 MD5 或 SHA-256 值，用于秒传校验和去重。 |
| **storage_bucket** | TEXT        | 存储桶名  | MinIO 中的 Bucket 名称。                                     |
| **storage_key**    | TEXT        | 存储路径  | MinIO 中的对象 Key（物理路径，建议使用 UUID 命名以防冲突）。 |
| **file_size**      | BIGINT      | 文件大小  | 单位：Bytes。                                                |
| **content_type**   | TEXT        | MIME 类型 | 如 `image/jpeg`，决定浏览器预览行为。                        |
| **extension**      | TEXT        | 后缀名    | 文件的物理后缀（如 `.pdf`, `.zip`），方便搜索和分类。        |
| **created_at**     | TIMESTAMPTZ | 创建时间  | 物理文件首次上传并入库的时间。                               |

------

### 2.5 业务文档表 (documents)

存储文档的业务属性及关联关系。通过 `file_id` 关联物理文件，支持一个物理文件被多个文档记录引用。

| **字段名**      | **类型**    | **描述**     | **说明**                                                     |
| --------------- | ----------- | ------------ | ------------------------------------------------------------ |
| **id**          | UUID        | 主键         | 默认使用 `gen_random_uuid()`。                               |
| **file_id**     | UUID        | 物理文件外键 | **关键外键**。关联 `files.id`。若物理文件删除，此字段可设为 NULL。 |
| **project_id**  | UUID        | 业务外键     | **必填**，关联所属项目。                                     |
| **software_id** | UUID        | 业务外键     | **可选**，关联具体软件。若为 NULL 则直属项目。               |
| **name**        | TEXT        | 文档名称     | 业务展示名称。用户修改此名称不会影响物理存储的文件名。       |
| **description** | TEXT        | 描述信息     | 对文档内容的简要说明。                                       |
| **extra_info**  | JSONB       | 扩展元数据   | 存储业务特有的元数据，如标签、审核状态、提取的关键词等。     |
| **created_at**  | TIMESTAMPTZ | 创建时间     | 文档记录创建时间。                                           |
| **updated_at**  | TIMESTAMPTZ | 更新时间     | 文档业务信息最后修改时间。                                   |
| **deleted_at**  | TIMESTAMPTZ | 逻辑删除时间 | 用于软删除。清理任务可据此判断是否需要减少物理文件引用计数。 |

## 3. 设计要点说明

1. **版本隔离**：通过 `ANALYSIS_VERSION` 表，系统可以轻松实现“对比两个版本的估算差异”。用户修改数据时，系统可以创建一个新版本，也可以在当前草稿版本上直接修改。
2. **溯源性**：`requirement_items` 的 `source_ref` 和 `function_points` 的 `logic_desc` 共同支撑了 PRD 要求的“高可解释性”，让用户知道每一个功能点是怎么算出来的。
3. **松耦合**：`estimation_results` 中的 `model_params` 和 `model_name` 允许未来接入不同的成本估算方法，而无需大幅改动表结构。
4. **可干预性**：`is_modified` 标记能帮助用户在界面上快速区分哪些是 AI 自动生成的，哪些是经过人工确认的。
5. **逻辑删除**：`projects` 表通过 `deleted_at` 实现逻辑删除，项目删除时仅写入删除时间；列表、详情、更新等查询默认仅返回 `deleted_at IS NULL` 的记录。
