# JARVIS 自动化评估体系

> 目标：量化每次核心改动的影响，建立回归测试护城河。

## 快速开始

```bash
# 确保后端已启动
python -m uvicorn main:app --host 127.0.0.1 --port 18200

# 运行全部评估用例
python -m evals

# 或指定后端地址
python -m evals --base http://127.0.0.1:18200

# 仅运行名称包含 memory 的用例
python -m evals --filter memory
```

## 评估项清单

| 用例 | 覆盖能力 | 通过标准 |
|------|---------|---------|
| `health` | 后端启动 / 健康检查 | `GET /health` 返回 `status: ok` |
| `config` | 配置接口 | `GET /api/config` 返回 `model_config` |
| `plugins_list` | 插件系统 | `GET /api/plugins` 返回插件数组 |
| `memory_crud` | 记忆增删查 | 创建 → 搜索命中 → 删除成功 |
| `memory_semantic` | 语义向量召回 | 相关查询能召回已保存记忆 |
| `chat_non_stream` | 非流式对话 | `POST /api/chat` 返回 assistant 回复 |
| `costs_summary` | 成本看板 | `GET /api/costs/summary` 返回汇总数据 |

## CI 集成建议

1. 在核心代码改动前执行 `python -m evals`。
2. 通过率需达到 100% 方可合并。
3. 失败用例需附带日志和修复说明。

## 扩展用例

新增用例只需在 `evals/cases.py` 中定义一个 `async def eval_xxx(client, base) -> EvalResult` 函数，并将其加入 `ALL_CASES` 列表。
