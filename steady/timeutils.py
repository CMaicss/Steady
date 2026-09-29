import math
from datetime import datetime, timezone

from .i18n import _, ngettext

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def parse_timestamp(value):
    if not isinstance(value, str):
        raise ValueError(_("Time must be an ISO 8601 string with a time zone"))
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(_("Time zone is missing"))
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
            raise ValueError(_("This time does not exist due to a daylight-saving transition. Choose another time"))
        return localized.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, OverflowError) as error:
        raise ValueError(_("Enter a valid date (YYYY-MM-DD) and time; times skipped by daylight saving are not allowed")) from error


def format_timestamp(value, seconds=False):
    if not value:
        return _("Not set")
    pattern = "%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M"
    return parse_timestamp(value).astimezone().strftime(pattern)


def deadline_status(deadline, closed_at=None, now=None):
    if not deadline:
        return "", ""
    due = parse_timestamp(deadline)
    if closed_at:
        return (_("Closed on time"), "success") if parse_timestamp(closed_at) <= due else (_("Closed late"), "warning")
    remaining = (due - (now or datetime.now(timezone.utc))).total_seconds()
    if 0 < remaining < 60:
        return _("Due soon"), "warning"
    overdue = remaining <= 0
    minutes = max(1, math.floor(abs(remaining) / 60) if overdue else math.ceil(remaining / 60))
    days, rest = divmod(minutes, 1440)
    hours, minutes = divmod(rest, 60)
    if days:
        duration = ngettext("{count} day", "{count} days", days).format(count=days)
        if hours:
            duration += " " + ngettext("{count} hour", "{count} hours", hours).format(count=hours)
    elif hours:
        duration = ngettext("{count} hour", "{count} hours", hours).format(count=hours)
        if minutes:
            duration += " " + ngettext("{count} minute", "{count} minutes", minutes).format(count=minutes)
    else:
        duration = ngettext("{count} minute", "{count} minutes", minutes).format(count=minutes)
    if overdue and remaining > -60:
        return _("Due now") if remaining == 0 else _("Less than 1 minute overdue"), "error"
    return (_("Overdue by {duration}").format(duration=duration), "error") if overdue else (_("{duration} left").format(duration=duration), "warning" if remaining < 86400 else "")
