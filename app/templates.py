"""Notification templates (spec §22-24, §C)."""

from app.models import AlphaEvent
from app.timeutil import fmt_display_hm, fmt_display_md_hm


def _detail_lines(event: AlphaEvent, start_fmt) -> list[str]:
    lines = []
    if event.project_name:
        lines.append(f"项目：{event.project_name}")
    if event.symbol:
        lines.append(f"代号：{event.symbol}")
    if start_fmt:
        lines.append(f"开始时间：{start_fmt}")
    if event.points_threshold is not None:
        lines.append(f"Alpha Points：{event.points_threshold}")
    if event.airdrop_amount is not None:
        token = f" {event.symbol}" if event.symbol else ""
        lines.append(f"空投数量：{event.airdrop_amount}{token}")
    if event.points_cost is not None:
        lines.append(f"消耗积分：{event.points_cost}")
    if event.claim_rule:
        lines.append(f"领取规则：{event.claim_rule}")
    return lines


def build_discovered(event: AlphaEvent, started: bool = False) -> tuple[str, str]:
    if started:
        title = "🚨 Binance Alpha 空投已经开始"
        body = [
            title,
            *_detail_lines(event, fmt_display_md_hm(event.start_time) if event.start_time else None),
            "⚠️ 请立即进入 Binance Alpha 页面领取。",
            "来源：Binance Wallet",
        ]
        return title, "\n".join(body)

    title = "🚨 Binance Alpha 空投预告"
    if event.project_name:
        body = [
            title,
            f"项目：{event.project_name}",
            f"代号：{event.symbol or '待公布'}",
            "状态：即将进行 Alpha 空投",
            f"开始时间：{fmt_display_md_hm(event.start_time) if event.start_time else '待官方公布'}",
            f"Alpha Points：{event.points_threshold if event.points_threshold is not None else '待公布'}",
            f"空投数量：{event.airdrop_amount if event.airdrop_amount is not None else '待公布'}",
            f"消耗积分：{event.points_cost if event.points_cost is not None else '待公布'}",
            "⚠️ 先到先得",
            "来源：Binance Wallet",
        ]
    else:
        body = [
            title,
            "币安官方已发布新的 Alpha 空投/领取预告。",
            "项目：暂未公布",
            "开始时间：待官方公布",
            "⚠️ 请关注后续官方规则更新。",
            "来源：Binance Wallet",
        ]
    return title, "\n".join(body)


def build_t10(event: AlphaEvent) -> tuple[str, str]:
    title = "⏰ Binance Alpha 空投10分钟后开始"
    body = [
        title,
        f"项目：{event.project_name or '待公布'}",
        f"代号：{event.symbol or '待公布'}",
        f"开始时间：{fmt_display_hm(event.start_time) if event.start_time else '待公布'}",
        f"Alpha Points：{event.points_threshold if event.points_threshold is not None else '待公布'}",
        f"空投数量：{event.airdrop_amount if event.airdrop_amount is not None else '待公布'}"
        + (f" {event.symbol}" if event.symbol and event.airdrop_amount is not None else ""),
        f"消耗积分：{event.points_cost if event.points_cost is not None else '待公布'}",
        "⚠️ 先到先得",
        "建议现在打开 Binance Alpha 页面准备领取。",
        "来源：Binance Wallet",
    ]
    return title, "\n".join(body)


_LABELS = {
    "project_name": "项目",
    "symbol": "代号",
    "start_time": "开始时间",
    "end_time": "结束时间",
    "points_threshold": "Alpha Points 要求",
    "points_cost": "消耗积分",
    "airdrop_amount": "空投数量",
    "claim_rule": "领取规则",
}


def build_updated(event: AlphaEvent, changes) -> tuple[str, str]:
    from app.timeutil import fmt_display_md_hm

    title = "🔄 Binance Alpha 空投信息更新"
    body = [
        title,
        f"项目：{event.project_name or '待公布'}",
        f"代号：{event.symbol or '待公布'}",
    ]
    for name, (old, new) in changes.fields.items():
        label = _LABELS.get(name, name)
        fmt = lambda v: (fmt_display_md_hm(v) if hasattr(v, "strftime") and v else str(v) if v else "待公布")
        body.append(f"{label}：{fmt(new)}" + (f"（原：{fmt(old)}）" if old else ""))
    if changes.start_time_changed and event.start_time:
        from app.reminders import scheduled_reminder_time

        t10 = scheduled_reminder_time(event)
        if t10:
            body.append(f"新的 T-10 提醒：{fmt_display_md_hm(t10)}")
    body.append("⚠️ 请以最新官方规则为准。")
    body.append("来源：Binance Wallet")
    return title, "\n".join(body)
