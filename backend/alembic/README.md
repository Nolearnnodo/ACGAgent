# Alembic 说明

本目录使用的迁移库是 `Alembic`。

Alembic 是 `SQLAlchemy` 官方生态中的数据库迁移工具，主要用于管理数据库结构（表、字段、索引、约束等）的版本变更。它解决的问题是：

- 代码中的 ORM 模型已经更新
- 但现有数据库里的真实表结构还停留在旧版本
- 需要一种可重复、可追踪、可回滚的方式去升级数据库

在本项目里，引入 Alembic 的直接原因是：

- `models/execution.py` 中为 `execution_runs` 增加了 `trigger_type`、`passage_id`
- 仅靠 `Base.metadata.create_all()` 不会修改已经存在的旧表
- 因此需要迁移脚本来补齐现有 SQLite 数据库的结构

## 当前接入策略

当前项目已经切换为：**把建表和结构演进统一交给 Alembic**。

也就是说：

1. 应用启动时不再自动执行 `create_all()`
2. 数据库表结构必须先通过 Alembic 迁移创建或升级
3. 应用服务启动前，应先执行 `alembic upgrade head`

这样做的好处是：

- 建表与结构变更有统一入口
- 数据库结构有明确版本历史
- 开发、测试、部署环境的 schema 更容易保持一致

## 目录结构

- `alembic.ini`
  - Alembic 主配置文件
- `env.py`
  - Alembic 运行环境配置
  - 当前会复用应用配置中的数据库地址，避免 Alembic 和应用指向不同数据库
- `script.py.mako`
  - 新建迁移脚本时使用的模板
- `versions/`
  - 所有迁移脚本都放在这里

## 当前已有迁移

### `20260423_0001_init.py`

这条迁移是当前项目的**初始完整建库迁移**，用于从空数据库直接创建完整 schema。

## 常用命令

以下命令默认在 `backend` 目录下执行。

### 1. 升级到最新版本

```bash
alembic upgrade head
```

作用：

- 把当前数据库升级到最新迁移版本

### 2. 查看当前数据库版本

```bash
alembic current
```

### 3. 查看迁移历史

```bash
alembic history
```

### 4. 新建一个空迁移

```bash
alembic revision -m "add some new columns"
```

这个命令会在 `versions/` 下生成一个新的迁移文件，你需要手动填写 `upgrade()` / `downgrade()`。

### 5. 基于模型自动生成迁移草稿

```bash
alembic revision --autogenerate -m "sync models"
```

注意：

- 自动生成只是“草稿”，不是最终结果
- 生成后必须人工检查
- 尤其是 SQLite 下的复杂变更，自动生成结果不一定完全可靠

## 后续推荐使用方式

当你以后修改了 `backend/app/models/` 下的 ORM 模型时，推荐流程如下：

1. 先修改模型代码
2. 生成迁移草稿：

```bash
alembic revision --autogenerate -m "describe your change"
```

3. 人工检查 `versions/` 下新生成的迁移脚本
4. 执行迁移：

```bash
alembic upgrade head
```

## 编写迁移时的注意事项

### 1. 不要依赖应用启动自动建表

当前项目已经不再使用 `create_all()` 自动建表，所有 schema 变更都应通过迁移完成。

### 2. SQLite 迁移能力有限

SQLite 对某些复杂 `ALTER TABLE` 支持不好，所以：

- 简单加列通常没问题
- 复杂改列、删列、改约束时要更加谨慎

### 3. 迁移脚本要尽量幂等

当前项目里的首条迁移使用了“先检查表/列/索引是否存在，再操作”的写法，这样更适合对已有开发库做修复。

### 4. `downgrade()` 不一定总能完全恢复数据

删除列、删表、改结构时，理论上可以写回滚，但如果涉及数据迁移，回滚通常要非常小心。

## 当前项目中的最佳实践建议

当前推荐这样做：

- 新环境先执行 `alembic upgrade head`
- 每次变更数据库结构时，都补 Alembic 迁移
- 不要再依赖删库重建或应用启动自动建表来同步结构
