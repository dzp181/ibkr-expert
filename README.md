# IBKR国际专家（ibkr-expert）

盈透证券（Interactive Brokers）中文专家：把"能不能开、多少钱、几点开盘、要不要交税"这类问题答准，也能连你本机已登录的 TWS / IB Gateway 做账户体检与下单复核。

## 类型

Agent 型（单个 AI 专家），内置技能 `skills/ibkr-assistant`。

## 功能

**知识层（不连任何券商接口）**
- 开户资格与材料核验：判断账户实体归属（IB LLC / IBKR 香港）、材料可行性、退回原因归类、可打印清单
- 佣金测算：美股 / 港股 / 期权 / 期货 / 换汇，固定式 vs 阶梯式年成本对比与盈亏平衡点
- 交易时段：美东↔北京双时区换算、夏冬令时切换、盘前盘后与隔夜交易规则
- 税务与资金：W-8BEN、1042-S、股息预扣、境外所得申报边界、同名电汇要求与退汇原因
- 客服与安全：各地区客服电话、中文支持时段、IB Key 与防诈

**实操层（本机 TWS / Gateway）**
- 账户体检：净值、现金、购买力、保证金占用、持仓成本与浮盈、集中度与现金拖累
- 行情与历史：报价快照、K 线、合约 conId、扫描器选股
- 订单：未成交订单、当日成交、whatIf 保证金与佣金预演、按用户明确参数下单或撤单

**安全闸门**：查询命令固定只读；下单默认 dry-run，需 `--execute`；实盘账户另需 `--live-confirm`。

## 使用示例

- 内地居民现在还能开盈透吗？需要哪些材料，我的情况算不算符合
- 帮我测算：每月约 20 笔美股，固定式和阶梯式佣金哪个更划算
- 连上我的 TWS，看账户净值、持仓集中度和保证金占用

## 目录结构

```
ibkr-expert/
├── .codebuddy-plugin/plugin.json
├── agents/ibkr-expert.md          # 专家人设、工作流、红线
├── skills/ibkr-assistant/         # 内置技能
│   ├── SKILL.md
│   ├── references/                # 开户/费率/时段/税务/客服/实盘 API
│   ├── scripts/ibkr.py|ibkr.sh    # 本地实操 CLI
│   ├── assets/ examples/
│   └── requirements.txt
├── avatars/expert.png
└── README.md
```

## 头像

头像为自定义品牌图，与 SkillHub 上架技能图标同一张，文件在 `avatars/expert.png`（512×512 px，PNG，约 367KB）。

`plugin.json` 的 `avatar` 必须写**包内相对路径** `avatars/expert.png`：专家中心上传校验会检查该路径是否存在，写外部 URL 会报「avatar 路径在包内不存在」。替换头像时同名覆盖即可，保持 512×512、PNG/JPG、≤500KB。

## 安装

**方式一：从 GitHub 克隆**

```bash
git clone https://github.com/dzp181/ibkr-expert.git \
  ~/.workbuddy/plugins/marketplaces/my-experts/plugins/ibkr-expert
```

**方式二：手动放置**

把本仓库内容拷到专家目录：

```
~/.workbuddy/plugins/marketplaces/my-experts/plugins/ibkr-expert/
```

**方式三：上传专家中心**

用 `package_expert.py` 打出 zip，在 WorkBuddy【专家中心】上传安装。

放置后注册一次即可在【专家中心 - 我的专家】看到：

```bash
python3 <expert-manager>/scripts/register_expert.py <expert-dir>
```

## 实操层前置

1. 本机运行已登录的 TWS 或 IB Gateway
2. `API → Settings → Enable ActiveX and Socket Clients` 勾选
3. `pip install -r skills/ibkr-assistant/requirements.txt`

端口：TWS 纸面 7497 / 实盘 7496；Gateway 纸面 4002 / 实盘 4001。

## 边界

不荐股、不预测涨跌、不代办开户、不指导材料造假与违规购汇、不托管账号凭据、不做自动化交易策略。详见 `agents/ibkr-expert.md` 的合规红线。

## 打包分享

```bash
python3 <expert-manager>/scripts/package_expert.py <expert-dir>
```
