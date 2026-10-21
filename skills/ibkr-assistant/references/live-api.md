# 实操层：接本机 TWS / IB Gateway

> 适用：用户要**查自己的账户、拉行情、看订单、下单撤单**，而不是问规则。
> 一句话原则：**查询随便跑，下单先预演，真发单要两道确认。**

## 前置条件（缺一不可）

1. 本机或同网段跑着 **TWS** 或 **IB Gateway**，且已登录账户。
2. TWS/Gateway 里打开 API：`File → Global Configuration → API → Settings`
   - 勾选 **Enable ActiveX and Socket Clients**
   - 记下 **Socket port**（见下表）
   - 建议勾 **Read-Only API**（只做查询时），要下单再取消勾选
   - 若 CLI 不在本机，把调用方 IP 加进 **Trusted IPs**
3. Python 3.9+ 且装了 `ib_insync`：`python3 -m pip install -r requirements.txt`

## 端口对照

| 环境 | TWS | IB Gateway |
|---|---|---|
| 纸面（Paper） | 7497 | 4002 |
| 实盘（Live） | 7496 | 4001 |

连错端口最典型的症状：**连上了但账户是空的**，或者报 `No account`。

## 账户号识别

- `DU` 开头 = 纸面模拟账户，亏的是假钱
- `U` 开头 = 实盘账户

CLI 用这个前缀判断风险等级：非 `DU` 的账户下单必须再加 `--live-confirm`。

## 命令速查

```bash
# 0. 先自检：连得上吗、连的是哪个账户
./scripts/ibkr.sh ping

# 1. 账户
./scripts/ibkr.sh account-summary              # 净值/现金/购买力/保证金
./scripts/ibkr.sh positions                    # 持仓与成本
./scripts/ibkr.sh portfolio                    # 市值与浮盈
./scripts/ibkr.sh pnl --wait 3                 # 当日/浮动/已实现

# 2. 行情
./scripts/ibkr.sh quote --symbol AAPL
./scripts/ibkr.sh quote --symbol 700 --sec-type STK --currency HKD   # 腾讯
./scripts/ibkr.sh quote --symbol EURUSD --sec-type CASH
./scripts/ibkr.sh historical --symbol AAPL --duration "30 D" --bar-size "1 day"
./scripts/ibkr.sh historical --symbol AAPL --duration "5 D" --bar-size "5 mins" --include-extended
./scripts/ibkr.sh contract-details --symbol ES --sec-type FUT --expiry 202612
./scripts/ibkr.sh scanner --scan-code TOP_PERC_GAIN --rows 20

# 3. 订单
./scripts/ibkr.sh preview-order --symbol AAPL --action BUY --quantity 10 --order-type LMT --limit-price 230
./scripts/ibkr.sh place-order   --symbol AAPL --action BUY --quantity 10 --order-type LMT --limit-price 230          # dry-run
./scripts/ibkr.sh place-order   --symbol AAPL --action BUY --quantity 10 --order-type LMT --limit-price 230 --execute # 真发单
./scripts/ibkr.sh open-orders
./scripts/ibkr.sh cancel-order --order-id 12345 --execute
./scripts/ibkr.sh executions
```

加 `--json` 给任何命令，输出结构化数据便于二次处理。

## 三道安全闸门

| 闸门 | 作用 | 绕过方式 |
|---|---|---|
| 查询命令强制 `readonly=True` | 物理上无法改账户状态 | 无（设计如此） |
| `place-order` / `cancel-order` 默认 dry-run | 只打印，不发送 | 加 `--execute` |
| 实盘账户 `--execute` 还需 `--live-confirm` | 防"以为在模拟盘" | 加 `--live-confirm` |

**给用户下单前，必须先跑一次 dry-run 并把这几项念一遍**：方向、数量、订单类型、限价/止损价、TIF、是否允许盘前盘后、预估保证金占用、账户是纸面还是实盘。等用户明确回"确认"，才加 `--execute`。

## 订单类型与适用场景

| 类型 | CLI 写法 | 说明 |
|---|---|---|
| 市价单 | `--order-type MKT` | 一定成交但价格不保证，**流动性差的票别用** |
| 限价单 | `--order-type LMT --limit-price X` | 最常用；隔夜交易只能用限价 |
| 止损单 | `--order-type STP --stop-price X` | 触发后变市价，跳空时滑点大 |
| 止损限价 | `--order-type "STP LMT" --stop-price X --limit-price Y` | 控价但可能不成交 |

TIF：`DAY`（当日有效）、`GTC`（撤销前有效，最长按 IBKR 规则）、`IOC`、`FOK`、`OPG`（开盘）。

## 常见报错与对策

| 现象 | 原因 | 处理 |
|---|---|---|
| `ConnectionRefusedError` / 502 | TWS 没开、API 没勾、端口错 | 开 TWS → 检查 Enable ActiveX and Socket Clients 与端口 |
| `Connect call failed ('127.0.0.1', 7497)` 但 TWS 开着 | 连的是 Gateway 端口或反之 | 7497/7496 是 TWS，4002/4001 是 Gateway |
| 账户列表为空 | 连错端口（实盘/纸面）、或登录了另一个账户 | `ping` 看 `account_type`，重新登录正确环境 |
| error 10089 / 10167 | 没订阅该市场行情，或未签市场数据协议 | 换 `--market-data-type 3`（延迟），或去 Client Portal 订阅 |
| error 162 | 历史数据请求过快（pacing） | 等 15 秒再请求，别循环拉 |
| error 10349 | 订单需要 TWS 里人工确认 | TWS 弹窗点确认，或关掉 "Bypass Order Precautions" 相关设置 |
| error 201 / 399 | 订单被拒：资金不足、数量不合法、合约不可交易 | 看 `whyHeld` 与 `log`，降数量或换合约 |
| `whatIf` 无返回 | 部分账户/合约不支持预演 | 跳过预演，改用 `account-summary` 看购买力是否够 |

## 与问答层的分工

- **规则类**（佣金怎么算、几点开盘、要不要 W-8BEN）→ 走 `references/`，不连 TWS。
- **数据类**（我账户现在多少钱、AAPL 现价、我的单成交没）→ 走 `scripts/ibkr.py`。
- 两边都要时：先用问答层给出规则与成本，再用实操层核对真实数字。

例：用户问"买 100 股 AAPL 要多少钱"——先用 `fees.md` 算佣金 1 美元（固定式），再用 `quote` 拿现价、`preview-order` 拿保证金占用，最后合并回答。

## 合规边界（比问答层更硬）

- **不代客决策**：不判断"该不该买"，只执行用户明确下达的参数；用户说不清数量/价格就先问。
- **不托管凭据**：不索取 IBKR 账号密码、不碰 IB Key 验证码；只连本机已登录的 TWS。
- **不绕过确认**：任何情况下不替用户加 `--execute` / `--live-confirm`，必须由用户当轮明确授权。
- **不承诺结果**：不保证成交价、不承诺收益；滑点、拒单、部分成交都要提前说明。
- 单笔金额较大（如超过账户净值 20%）时，主动提示仓位集中风险，但不替用户改参数。
