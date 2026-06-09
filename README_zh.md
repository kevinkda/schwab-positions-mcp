# schwab-positions-mcp

> **🔒 设计层面只读 — 详见 [docs/SECURITY.md](docs/SECURITY.md)**
>
> 无 `place_order`、无 `cancel_order`、无 `replace_order`。本 MCP 服务器
> 仅向 LLM Agent 暴露 Schwab 账户状态，**绝不**暴露任何下单/撤单接口。
> 由 5 层边界强制（运行时白名单 + 启动警告 + 工具面审计 + CI grep 卡点 + 拒绝测试）。

[English](README.md)

`schwab-positions-mcp` 是一个 MCP（Model Context Protocol）服务器，向任意
兼容 MCP 的 LLM 客户端（Claude Desktop、Cursor 等）暴露 Charles Schwab
经纪账户的**只读**视图——持仓、订单、交易记录、账户余额。配套仓库
[`schwab-marketdata-mcp`](https://github.com/kevinkda/schwab-marketdata-mcp)
暴露 Schwab Market Data Production 接口。两仓刻意分开，让交易账户凭据集
独立进程、独立配置目录。

## 工具列表（8 个）

### 账户 / 持仓（6 个）

| 工具                     | 说明                                                                                       |
| ------------------------ | ------------------------------------------------------------------------------------------ |
| `get_accounts`           | 列出所有关联的 Schwab 账户；可选 `fields=["positions"]` 内联展开持仓。                     |
| `get_account_numbers`    | 返回 `accountNumber` → 加密 `account_hash`（即 `hashValue`）映射，是其余工具的前置依赖。   |
| `get_account_positions`  | 获取单账户持仓 + 余额；若缓存启用则把持仓快照写入本地 DuckDB。                             |
| `get_orders_history`     | 查询两个时区感知 datetime 之间的历史订单（Schwab 服务端最长 60 天回溯）。                  |
| `get_transactions`       | 查询两个 ISO 日期之间的交易（TRADE / DIVIDEND_OR_INTEREST 等）。                           |
| `get_account_summary`    | 单账户聚合：持仓数、总市值、总盈亏、现金、购买力、余额表。                                 |

### Meta（2 个）

| 工具              | 说明                                                                                   |
| ----------------- | -------------------------------------------------------------------------------------- |
| `health_check`    | 就绪检查——上报凭据 / token 状态，**不**联通 Schwab。                                   |
| `get_server_info` | 服务器元信息——版本、平台、**`is_read_only: true`**、工具列表。                         |

## 安装

```bash
git clone https://github.com/kevinkda/schwab-positions-mcp.git
cd schwab-positions-mcp
uv sync --extra dev
```

需 Python ≥ 3.11，并已在 Schwab 开发者门户注册 Trader API 客户端应用
（详见 [docs/REGISTER.md](docs/REGISTER.md)）。

## 配置

```bash
cp .env.example .env
# 编辑 .env，填入 SCHWAB_API_KEY、SCHWAB_APP_SECRET、SCHWAB_CALLBACK_URL
```

## OAuth（一次性）

```bash
uv run python -m schwab_positions_mcp.auth login_flow
# 若浏览器自动跳转坏掉，改用：
uv run python -m schwab_positions_mcp.auth manual_flow
```

Token 存于 `~/.config/schwab-positions-mcp/token.json`（权限 `0o600`，与
`schwab-marketdata-mcp` 的目录隔离）。

> **OAuth scope 说明。** Schwab 对所有 token 强制 `trade` scope，包括
> 只读 positions / orders / transactions 接口。因此 token 在能力层面
> *具备*下单权限。**本 MCP 服务器在代码层全部拦截下单调用**，被劫持的
> LLM 也无法用它真正交易。详见 [docs/SECURITY.md](docs/SECURITY.md)
> 5 层契约。

## 运行

```bash
uv run schwab-positions-mcp           # MCP stdio 传输
# 或
uv run python -m schwab_positions_mcp # 等价
```

按常见 MCP `command` + `args` 形式接入 Claude Desktop / Cursor。

## 安全

- **只读契约：** [docs/SECURITY.md](docs/SECURITY.md)。
- **威胁模型：** [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md)。
- **Token 存储：** `~/.config/schwab-positions-mcp/token.json`，`0o600`，
  绝不入库。
- **CI：** `test.yml`（lint / 类型 / 单测）、`codeql.yml`（CodeQL）、
  `security-grep.yml`（no-trade 卡点）。

## 缓存

本地 DuckDB 缓存可持久化只读快照（持仓 / 订单 / 交易）到
`~/.local/state/schwab-positions-mcp/cache.duckdb`，LLM Agent 可基于此
做“上周到现在变化”类查询而不必反复打 Schwab。

缓存**默认关闭（需显式开启）**——不创建 DuckDB 文件，每次工具调用都
实时打 Schwab。通过 `SCHWAB_POSITIONS_CACHE_ENABLED=true`（也接受
`1` / `yes` / `on`）显式启用。关闭时工具返回
`_cache_status: "skipped:disabled"`，响应结构其余不变。

## 许可证

MIT — 详见 [LICENSE](LICENSE)。
