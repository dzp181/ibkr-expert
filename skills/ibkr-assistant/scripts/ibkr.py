#!/usr/bin/env python3
"""IBKR 实操 CLI —— 通过 TWS / IB Gateway 读写账户、行情与订单。

设计原则（与问答层分开，安全优先）：
1. 查询类命令一律只读连接（readonly=True），不可能误改账户状态。
2. 下单默认 dry-run：只打印订单参数与保证金预演，必须显式 --execute 才真正提交。
3. 实盘账户（非 DU 开头）下单，还要再加 --live-confirm，防止顺手打真钱。
4. 所有命令支持 --json，便于 agent 解析；失败统一非零退出码。

依赖：ib_insync（或已停维护后的社区分支 ib_async）。
    python3 -m pip install ib_insync

连接参数可用环境变量覆盖：
    IBKR_HOST=127.0.0.1  IBKR_PORT=7497  IBKR_CLIENT_ID=1  IBKR_ACCOUNT=DU123456
端口约定：
    7497 TWS 纸面 · 7496 TWS 实盘 · 4002 Gateway 纸面 · 4001 Gateway 实盘
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional

_IB_IMPORT_ERROR: Optional[BaseException] = None
_IB_MODULE = ""

try:  # ib_insync 是主流包；作者停止维护后出现了 API 兼容的 ib_async
    from ib_insync import (  # type: ignore
        IB,
        Contract,
        Forex,
        Future,
        LimitOrder,
        MarketOrder,
        Option,
        ScannerSubscription,
        Stock,
        StopLimitOrder,
        StopOrder,
    )

    _IB_MODULE = "ib_insync"
except Exception as exc_insync:  # pragma: no cover
    _IB_IMPORT_ERROR = exc_insync
    try:
        from ib_async import (  # type: ignore
            IB,
            Contract,
            Forex,
            Future,
            LimitOrder,
            MarketOrder,
            Option,
            ScannerSubscription,
            Stock,
            StopLimitOrder,
            StopOrder,
        )

        _IB_MODULE = "ib_async"
        _IB_IMPORT_ERROR = None
    except Exception as exc_async:  # pragma: no cover
        _IB_IMPORT_ERROR = exc_insync or exc_async


DONE_STATUSES = {"ApiCancelled", "Cancelled", "Filled", "Inactive"}
SEC_TYPES = ["STK", "OPT", "FUT", "CASH", "CFD", "IND", "BOND", "CRYPTO", "FUND"]


# ---------------------------------------------------------------- 基础设施


def fail(message: str, code: int = 2) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return code


def require_ib() -> Optional[int]:
    if _IB_IMPORT_ERROR is not None:
        return fail(
            "缺少 ib_insync 依赖。安装：python3 -m pip install ib_insync\n"
            f"原始错误：{_IB_IMPORT_ERROR}",
            3,
        )
    return None


def env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def add_conn_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=env("IBKR_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(env("IBKR_PORT", "7497")))
    parser.add_argument("--client-id", type=int, default=int(env("IBKR_CLIENT_ID", "1")))
    parser.add_argument("--account", default=env("IBKR_ACCOUNT", ""))
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")


def connect(args: argparse.Namespace, readonly: bool) -> Any:
    ib = IB()
    ib.connect(
        args.host,
        args.port,
        clientId=args.client_id,
        timeout=args.timeout,
        readonly=readonly,
        account=args.account or "",
    )
    return ib


def emit(data: Any, as_json: bool, human: Optional[str] = None) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print(human if human is not None else json.dumps(data, ensure_ascii=False, indent=2, default=str))


def money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def is_paper(account: str) -> bool:
    return account.upper().startswith("DU")


def clean_price(value: Any) -> Optional[float]:
    """IB 用 1.797e308（UNSET_DOUBLE）表示"未设置"，展示时转成 None。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if abs(num) >= 1e300 or num != num:  # UNSET_DOUBLE 或 NaN
        return None
    return num


def stock_contract(args: argparse.Namespace) -> Any:
    """按 --sec-type 构造合约。股票走 SMART，外汇走 IDEALPRO。"""
    symbol = args.symbol.upper()
    sec = args.sec_type.upper()
    currency = args.currency.upper()
    if sec == "STK":
        return Stock(symbol, args.exchange or "SMART", currency)
    if sec == "CASH":
        pair = symbol if len(symbol) == 6 else f"{symbol}{args.currency_pair_suffix}".upper()
        if len(pair) != 6:
            raise ValueError("外汇请写成 6 位货币对，如 EURUSD；或用 --currency-pair-suffix 补全")
        return Forex(pair)
    if sec == "FUT":
        return Future(symbol=symbol, lastTradeDateOrContractMonth=args.expiry or "", exchange=args.exchange or "CME", currency=currency)
    if sec == "OPT":
        return Option(
            symbol=symbol,
            lastTradeDateOrContractMonth=args.expiry or "",
            strike=float(args.strike or 0),
            right=args.right or "C",
            exchange=args.exchange or "SMART",
            currency=currency,
            multiplier=args.multiplier or "",
        )
    return Contract(symbol=symbol, secType=sec, exchange=args.exchange or "SMART", currency=currency)


def qualify(ib: Any, contract: Any) -> Any:
    details = ib.qualifyContracts(contract)
    if not details:
        raise RuntimeError(f"合约无法识别：{contract}")
    return details[0]


# ---------------------------------------------------------------- 账户类命令


def cmd_ping(args: argparse.Namespace) -> int:
    ib = connect(args, readonly=True)
    try:
        accounts = ib.managedAccounts()
        ib.reqMarketDataType(1)
        payload = {
            "connected": ib.isConnected(),
            "module": _IB_MODULE,
            "server_version": ib.client.serverVersion(),
            "connection_time": str(ib.client.connectionTime()),
            "accounts": accounts,
            "account_type": ["paper" if is_paper(a) else "live" for a in accounts],
            "market_data_type": 1,
            "host": args.host,
            "port": args.port,
        }
        human = (
            f"已连接 {'/'.join(accounts) or '(无账户)'}  "
            f"serverVersion={payload['server_version']}  "
            f"类型={'纸面' if all(is_paper(a) for a in accounts) else '含实盘'}"
        )
        emit(payload, args.json, human)
        return 0
    finally:
        ib.disconnect()


def cmd_account_summary(args: argparse.Namespace) -> int:
    ib = connect(args, readonly=True)
    try:
        rows = []
        for item in ib.accountSummary():
            if args.account and item.account != args.account:
                continue
            rows.append(
                {
                    "account": item.account,
                    "tag": item.tag,
                    "value": item.value,
                    "currency": item.currency,
                }
            )
        keep = {
            "NetLiquidation",
            "TotalCashValue",
            "AvailableFunds",
            "BuyingPower",
            "GrossPositionValue",
            "MaintMarginReq",
            "InitMarginReq",
            "ExcessLiquidity",
            "AccruedCash",
        }
        key_rows = [r for r in rows if r["tag"] in keep]
        human_lines = [f"{r['tag']:<20} {r['value']:>15} {r['currency']}" for r in key_rows] or [
            f"{r['tag']:<20} {r['value']:>15} {r['currency']}" for r in rows
        ]
        emit(rows, args.json, "\n".join(human_lines))
        return 0
    finally:
        ib.disconnect()


def cmd_positions(args: argparse.Namespace) -> int:
    ib = connect(args, readonly=True)
    try:
        rows = []
        for p in ib.positions():
            if args.account and p.account != args.account:
                continue
            c = p.contract
            rows.append(
                {
                    "account": p.account,
                    "symbol": c.symbol,
                    "secType": c.secType,
                    "currency": c.currency,
                    "position": p.position,
                    "avgCost": p.avgCost,
                    "conId": c.conId,
                }
            )
        human = "\n".join(
            f"{r['symbol']:<10} {r['secType']:<5} {r['position']:>10} @ {money(r['avgCost'])} {r['currency']}"
            for r in rows
        ) or "无持仓"
        emit(rows, args.json, human)
        return 0
    finally:
        ib.disconnect()


def cmd_portfolio(args: argparse.Namespace) -> int:
    ib = connect(args, readonly=True)
    try:
        rows = []
        for item in ib.portfolio():
            c = item.contract
            rows.append(
                {
                    "account": item.account,
                    "symbol": c.symbol,
                    "secType": c.secType,
                    "currency": c.currency,
                    "position": item.position,
                    "marketPrice": item.marketPrice,
                    "marketValue": item.marketValue,
                    "averageCost": item.averageCost,
                    "unrealizedPNL": item.unrealizedPNL,
                    "realizedPNL": item.realizedPNL,
                }
            )
        human = "\n".join(
            f"{r['symbol']:<10} {r['position']:>8}  市值 {money(r['marketValue']):>12}  "
            f"浮盈 {money(r['unrealizedPNL']):>10} {r['currency']}"
            for r in rows
        ) or "无持仓"
        emit(rows, args.json, human)
        return 0
    finally:
        ib.disconnect()


def cmd_pnl(args: argparse.Namespace) -> int:
    ib = connect(args, readonly=True)
    try:
        accounts = [args.account] if args.account else ib.managedAccounts()
        out = []
        for acct in accounts:
            pnl = ib.reqPnL(acct)
            ib.sleep(args.wait)
            out.append(
                {
                    "account": acct,
                    "dailyPnL": pnl.dailyPnL,
                    "unrealizedPnL": pnl.unrealizedPnL,
                    "realizedPnL": pnl.realizedPnL,
                }
            )
            ib.cancelPnL(pnl)
        human = "\n".join(
            f"{r['account']}  当日 {money(r['dailyPnL'])}  浮动 {money(r['unrealizedPnL'])}  已实现 {money(r['realizedPnL'])}"
            for r in out
        )
        emit(out, args.json, human)
        return 0
    finally:
        ib.disconnect()


# ---------------------------------------------------------------- 行情类命令


def cmd_quote(args: argparse.Namespace) -> int:
    ib = connect(args, readonly=True)
    try:
        ib.reqMarketDataType(args.market_data_type)
        contract = qualify(ib, stock_contract(args))
        tickers = ib.reqTickers(contract)
        ib.sleep(args.wait)
        t = tickers[0]
        payload = {
            "symbol": contract.symbol,
            "secType": contract.secType,
            "conId": contract.conId,
            "bid": t.bid,
            "ask": t.ask,
            "last": t.last,
            "close": t.close,
            "volume": t.volume,
            "marketDataType": t.marketDataType,
            "note": "marketDataType 1=实时 2=冻结 3=延迟 4=延迟冻结；延迟数据来自订阅缺失时的兜底",
        }
        human = (
            f"{contract.symbol}  买 {t.bid}  卖 {t.ask}  最新 {t.last}  昨收 {t.close}  "
            f"量 {t.volume}  类型{t.marketDataType}"
        )
        emit(payload, args.json, human)
        return 0
    finally:
        ib.disconnect()


def cmd_historical(args: argparse.Namespace) -> int:
    ib = connect(args, readonly=True)
    try:
        contract = qualify(ib, stock_contract(args))
        what = args.what_to_show or ("MIDPOINT" if contract.secType == "CASH" else "TRADES")
        bars = ib.reqHistoricalData(
            contract,
            endDateTime=args.end or "",
            durationStr=args.duration,
            barSizeSetting=args.bar_size,
            whatToShow=what,
            useRTH=not args.include_extended,
            formatDate=1,
        )
        rows = [
            {
                "date": str(b.date),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "average": b.average,
                "barCount": b.barCount,
            }
            for b in bars
        ]
        human = "\n".join(f"{r['date']}  O{r['open']} H{r['high']} L{r['low']} C{r['close']} V{r['volume']}" for r in rows)
        emit(rows, args.json, human or "无数据（检查时段、合约或历史数据权限）")
        return 0
    finally:
        ib.disconnect()


def cmd_contract_details(args: argparse.Namespace) -> int:
    ib = connect(args, readonly=True)
    try:
        contract = qualify(ib, stock_contract(args))
        payload = {
            "conId": contract.conId,
            "symbol": contract.symbol,
            "secType": contract.secType,
            "exchange": contract.exchange,
            "primaryExchange": contract.primaryExchange,
            "currency": contract.currency,
            "localSymbol": contract.localSymbol,
            "tradingClass": contract.tradingClass,
            "multiplier": contract.multiplier,
        }
        emit(payload, args.json, "\n".join(f"{k:<16} {v}" for k, v in payload.items()))
        return 0
    finally:
        ib.disconnect()


def cmd_scanner(args: argparse.Namespace) -> int:
    ib = connect(args, readonly=True)
    try:
        sub = ScannerSubscription(
            instrument=args.instrument,
            locationCode=args.location_code,
            scanCode=args.scan_code,
            numberOfRows=args.rows,
        )
        data = ib.reqScannerData(sub)
        rows = [
            {
                "rank": d.rank,
                "symbol": d.contractDetails.contract.symbol,
                "secType": d.contractDetails.contract.secType,
                "conId": d.contractDetails.contract.conId,
                "distance": d.distance,
                "benchmark": d.benchmark,
                "projection": d.projection,
            }
            for d in data
        ]
        human = "\n".join(f"{r['rank']:>3}. {r['symbol']:<8} {r['secType']:<4} {r['projection']}" for r in rows)
        emit(rows, args.json, human or "无扫描结果")
        return 0
    finally:
        ib.disconnect()


# ---------------------------------------------------------------- 订单类命令


def build_order(args: argparse.Namespace) -> Any:
    if args.quantity <= 0:
        raise ValueError("--quantity 必须大于 0")
    action = args.action.upper()
    if action not in {"BUY", "SELL"}:
        raise ValueError("--action 只能是 BUY 或 SELL")
    order_type = args.order_type.upper()
    if order_type == "MKT":
        order = MarketOrder(action, args.quantity)
    elif order_type == "LMT":
        if args.limit_price is None:
            raise ValueError("限价单必须给 --limit-price")
        order = LimitOrder(action, args.quantity, args.limit_price)
    elif order_type in {"STP", "STOP"}:
        if args.stop_price is None:
            raise ValueError("止损单必须给 --stop-price")
        order = StopOrder(action, args.quantity, args.stop_price)
    elif order_type in {"STP LMT", "STPLMT"}:
        if args.stop_price is None or args.limit_price is None:
            raise ValueError("止损限价单必须同时给 --stop-price 与 --limit-price")
        order = StopLimitOrder(action, args.quantity, args.limit_price, args.stop_price)
    else:
        raise ValueError(f"不支持的订单类型：{order_type}（可选 MKT / LMT / STP / STP LMT）")
    order.tif = args.tif
    if args.outside_rth:
        order.outsideRth = True
    return order


def order_preview(ib: Any, contract: Any, order: Any, args: argparse.Namespace, account: str) -> dict:
    payload = {
        "account": account,
        "account_type": "paper" if is_paper(account) else "live",
        "symbol": contract.symbol,
        "secType": contract.secType,
        "conId": contract.conId,
        "action": order.action,
        "quantity": float(order.totalQuantity),
        "orderType": order.orderType,
        "limitPrice": clean_price(getattr(order, "lmtPrice", None)),
        "stopPrice": clean_price(getattr(order, "auxPrice", None)),
        "tif": order.tif,
        "outsideRth": bool(getattr(order, "outsideRth", False)),
    }
    try:
        state = ib.whatIfOrder(contract, order)
        if state is not None:
            payload["whatIf"] = {
                "initMarginChange": state.initMarginChange,
                "maintMarginChange": state.maintMarginChange,
                "equityWithLoanChange": state.equityWithLoanChange,
                "commission": state.commission,
                "maxCommission": state.maxCommission,
                "warningText": state.warningText,
            }
    except Exception as exc:  # noqa: BLE001 - whatIf 对部分账户/合约不可用
        payload["whatIfError"] = str(exc)
    return payload


def cmd_preview_order(args: argparse.Namespace) -> int:
    order = build_order(args)  # 先校验参数，避免无谓连接
    ib = connect(args, readonly=True)
    try:
        contract = qualify(ib, stock_contract(args))
        account = args.account or (ib.managedAccounts() or [""])[0]
        payload = order_preview(ib, contract, order, args, account)
        emit(payload, args.json, "\n".join(f"{k:<20} {v}" for k, v in payload.items() if k != "whatIf")
             + ("\n" + str(payload.get("whatIf", "")) if payload.get("whatIf") else ""))
        return 0
    finally:
        ib.disconnect()


def cmd_place_order(args: argparse.Namespace) -> int:
    order = build_order(args)  # 先校验参数，避免无谓连接
    # 双重闸门：--execute 才发单；实盘再加 --live-confirm
    ib = connect(args, readonly=not args.execute)
    try:
        contract = qualify(ib, stock_contract(args))
        account = args.account or (ib.managedAccounts() or [""])[0]
        payload = order_preview(ib, contract, order, args, account)

        if not args.execute:
            payload["submitted"] = False
            payload["next_step"] = "确认无误后追加 --execute 才会真正下单"
            emit(
                payload,
                args.json,
                "【DRY-RUN 未提交】\n"
                + "\n".join(f"{k:<18} {v}" for k, v in payload.items() if k not in {"whatIf", "next_step"})
                + f"\n{payload['next_step']}",
            )
            return 0

        if not is_paper(account) and not args.live_confirm:
            return fail(
                f"账户 {account} 是实盘账户。确认真要下单请追加 --live-confirm（本次未提交任何订单）",
                4,
            )

        trade = ib.placeOrder(contract, order)
        for _ in range(int(args.wait / 0.5) + 1):
            ib.sleep(0.5)
            if trade.orderStatus.status in DONE_STATUSES:
                break
        payload.update(
            {
                "submitted": True,
                "orderId": trade.order.orderId,
                "status": trade.orderStatus.status,
                "filled": trade.orderStatus.filled,
                "remaining": trade.orderStatus.remaining,
                "avgFillPrice": trade.orderStatus.avgFillPrice,
                "whyHeld": trade.orderStatus.whyHeld,
                "log": [f"{e.status}: {e.message}" for e in trade.log][-5:],
            }
        )
        emit(
            payload,
            args.json,
            f"已提交 orderId={payload['orderId']} 状态={payload['status']} "
            f"成交{payload['filled']}/{payload['quantity']} 均价{payload['avgFillPrice']}",
        )
        return 0
    finally:
        ib.disconnect()


def cmd_open_orders(args: argparse.Namespace) -> int:
    ib = connect(args, readonly=True)
    try:
        ib.reqAllOpenOrders()
        ib.sleep(args.wait)
        rows = []
        for trade in ib.openTrades():
            c = trade.contract
            rows.append(
                {
                    "orderId": trade.order.orderId,
                    "account": trade.order.account,
                    "symbol": c.symbol,
                    "secType": c.secType,
                    "action": trade.order.action,
                    "quantity": float(trade.order.totalQuantity),
                    "orderType": trade.order.orderType,
                    "limitPrice": clean_price(getattr(trade.order, "lmtPrice", None)),
                    "status": trade.orderStatus.status,
                    "filled": trade.orderStatus.filled,
                    "remaining": trade.orderStatus.remaining,
                    "whyHeld": trade.orderStatus.whyHeld,
                }
            )
        human = "\n".join(
            f"#{r['orderId']:<8} {r['symbol']:<8} {r['action']:<4} {r['quantity']:>8} "
            f"{r['orderType']:<7} {r['status']:<10} 成交{r['filled']}"
            for r in rows
        ) or "无未成交订单"
        emit(rows, args.json, human)
        return 0
    finally:
        ib.disconnect()


def cmd_cancel_order(args: argparse.Namespace) -> int:
    ib = connect(args, readonly=not args.execute)
    try:
        ib.reqAllOpenOrders()
        ib.sleep(1.0)
        target = None
        for trade in ib.openTrades():
            if trade.order.orderId == args.order_id:
                target = trade
                break
        if target is None:
            return fail(f"未找到 orderId={args.order_id} 的未成交订单（先用 open-orders 查一次）", 5)
        if not args.execute:
            emit(
                {
                    "orderId": args.order_id,
                    "symbol": target.contract.symbol,
                    "would_cancel": True,
                    "next_step": "追加 --execute 才会真正撤单",
                },
                args.json,
                f"【DRY-RUN 未撤单】#{args.order_id} {target.contract.symbol}；追加 --execute 才执行",
            )
            return 0
        ib.cancelOrder(target.order)
        ib.sleep(1.0)
        emit(
            {"orderId": args.order_id, "cancelled": target.orderStatus.status in DONE_STATUSES},
            args.json,
            f"已发送撤单 #{args.order_id}，当前状态 {target.orderStatus.status}",
        )
        return 0
    finally:
        ib.disconnect()


def cmd_executions(args: argparse.Namespace) -> int:
    ib = connect(args, readonly=True)
    try:
        fills = ib.reqExecutions()
        rows = [
            {
                "time": str(f.time),
                "account": f.execution.acctNumber,
                "symbol": f.contract.symbol,
                "secType": f.contract.secType,
                "side": f.execution.side,
                "shares": f.execution.shares,
                "price": f.execution.price,
                "avgPrice": f.execution.avgPrice,
                "exchange": f.execution.exchange,
                "orderId": f.execution.orderId,
            }
            for f in fills
        ]
        human = "\n".join(
            f"{r['time']} {r['symbol']:<8} {r['side']:<4} {r['shares']:>8} @ {r['price']}" for r in rows
        ) or "当日无成交"
        emit(rows, args.json, human)
        return 0
    finally:
        ib.disconnect()


# ---------------------------------------------------------------- 参数装配


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ibkr",
        description="IBKR 实操 CLI：账户 / 行情 / 订单（默认只读、下单默认 dry-run）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func: Any, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        add_conn_args(p)
        p.set_defaults(func=func)
        return p

    add("ping", cmd_ping, "连通性与账户类型自检")
    add("account-summary", cmd_account_summary, "账户资金摘要")
    add("positions", cmd_positions, "当前持仓")
    add("portfolio", cmd_portfolio, "组合市值与盈亏")
    p = add("pnl", cmd_pnl, "当日/浮动/已实现盈亏")
    p.add_argument("--wait", type=float, default=2.0)

    def add_contract_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--symbol", required=True)
        p.add_argument("--sec-type", default="STK", choices=SEC_TYPES)
        p.add_argument("--exchange", default="")
        p.add_argument("--currency", default="USD")
        p.add_argument("--currency-pair-suffix", default="", help="CASH 且符号不足 6 位时拼接，如 USD")
        p.add_argument("--expiry", default="", help="FUT/OPT 到期，如 202606 或 20260619")
        p.add_argument("--strike", default="", help="OPT 行权价")
        p.add_argument("--right", default="C", choices=["C", "P"], help="OPT 看涨/看跌")
        p.add_argument("--multiplier", default="", help="OPT 合约乘数")

    p = add("quote", cmd_quote, "实时/延迟报价快照")
    add_contract_args(p)
    p.add_argument("--market-data-type", type=int, default=1, choices=[1, 2, 3, 4])
    p.add_argument("--wait", type=float, default=2.0)

    p = add("historical", cmd_historical, "历史 K 线")
    add_contract_args(p)
    p.add_argument("--duration", default="30 D")
    p.add_argument("--bar-size", default="1 day")
    p.add_argument("--what-to-show", default="")
    p.add_argument("--end", default="")
    p.add_argument("--include-extended", action="store_true", help="包含盘前盘后（useRTH=False）")

    p = add("contract-details", cmd_contract_details, "合约识别与 conId")
    add_contract_args(p)

    p = add("scanner", cmd_scanner, "市场扫描器")
    p.add_argument("--instrument", default="STK")
    p.add_argument("--location-code", default="STK.US.MAJOR")
    p.add_argument("--scan-code", default="TOP_PERC_GAIN")
    p.add_argument("--rows", type=int, default=25)

    def add_order_args(p: argparse.ArgumentParser) -> None:
        add_contract_args(p)
        p.add_argument("--action", required=True, choices=["BUY", "SELL", "buy", "sell"])
        p.add_argument("--quantity", type=float, required=True)
        p.add_argument("--order-type", default="MKT", help="MKT / LMT / STP / STP LMT")
        p.add_argument("--limit-price", type=float)
        p.add_argument("--stop-price", type=float)
        p.add_argument("--tif", default="DAY", choices=["DAY", "GTC", "IOC", "FOK", "OPG"])
        p.add_argument("--outside-rth", action="store_true")

    p = add("preview-order", cmd_preview_order, "订单预演（whatIf 保证金与佣金，绝不下单）")
    add_order_args(p)

    p = add("place-order", cmd_place_order, "下单（默认 dry-run，需 --execute）")
    add_order_args(p)
    p.add_argument("--execute", action="store_true", help="真正提交订单")
    p.add_argument("--live-confirm", action="store_true", help="实盘账户下单的二次确认")
    p.add_argument("--wait", type=float, default=10.0)

    p = add("open-orders", cmd_open_orders, "未成交订单")
    p.add_argument("--wait", type=float, default=2.0)

    p = add("cancel-order", cmd_cancel_order, "撤单（默认 dry-run，需 --execute）")
    p.add_argument("--order-id", type=int, required=True)
    p.add_argument("--execute", action="store_true")

    add("executions", cmd_executions, "当日成交记录")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    blocked = require_ib()
    if blocked is not None:
        return blocked
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return fail("已中断", 130)
    except Exception as exc:  # noqa: BLE001 - CLI 顶层兜底
        return fail(f"{type(exc).__name__}: {exc}", 1)


if __name__ == "__main__":
    raise SystemExit(main())
