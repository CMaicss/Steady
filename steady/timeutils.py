import math
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def parse_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("时间必须为带时区的 ISO 8601 字符串")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("时间缺少时区")
    return result


def normalize_timestamp(value):
    return parse_timestamp(value).astimezone(timezone.utc).isoformat(timespec="microseconds")


def local_deadline(date_text, hour, minute):
    try:
        selected = datetime.strptime(date_text.strip(), "%Y-%m-%d").replace(
            hour=int(hour), minute=int(minute)
        )
        localized = selected.astimezone()
        if localized.replace(tzinfo=None) != selected:
            raise ValueError("所选时间因夏令时切换而不存在，请选择其他时间")
        return localized.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, OverflowError) as error:
        raise ValueError("请输入有效日期（YYYY-MM-DD）和时间；夏令时跳过的时间不可选") from error


def format_timestamp(value, seconds=False):
    if not value:
        return "未设置"
    pattern = "%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M"
    return parse_timestamp(value).astimezone().strftime(pattern)


def deadline_status(deadline, closed_at=None, now=None):
    if not deadline:
        return "", ""
    due = parse_timestamp(deadline)
    if closed_at:
        return ("按时闭环", "success") if parse_timestamp(closed_at) <= due else ("逾期闭环", "warning")
    remaining = (due - (now or datetime.now(timezone.utc))).total_seconds()
    if 0 < remaining < 60:
        return "即将到期", "warning"
    overdue = remaining <= 0
    minutes = max(1, math.floor(abs(remaining) / 60) if overdue else math.ceil(remaining / 60))
    days, rest = divmod(minutes, 1440)
    hours, minutes = divmod(rest, 60)
    if days:
        duration = f"{days} 天" + (f" {hours} 小时" if hours else "")
    elif hours:
        duration = f"{hours} 小时" + (f" {minutes} 分钟" if minutes else "")
    else:
        duration = f"{minutes} 分钟"
    if overdue and remaining > -60:
        return "已到期" if remaining == 0 else "已逾期不足 1 分钟", "error"
    return (f"已逾期 {duration}", "error") if overdue else (f"剩余 {duration}", "warning" if remaining < 86400 else "")
