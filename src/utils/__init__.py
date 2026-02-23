# Utility Functions Module
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

# Indian Standard Time timezone
IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    """Get current datetime in IST timezone."""
    return datetime.now(IST)


def today_ist() -> date:
    """Get today's date in IST timezone."""
    return datetime.now(IST).date()


def time_now_ist() -> time:
    """Get current time in IST timezone."""
    return datetime.now(IST).time()


def make_ist_aware(dt: datetime) -> datetime:
    """
    Convert naive datetime to IST-aware datetime.
    
    Args:
        dt: Naive datetime
        
    Returns:
        IST-aware datetime
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def market_time(hour: int, minute: int = 0) -> datetime:
    """
    Create a datetime for today at the given market time (IST).
    
    Args:
        hour: Hour (0-23)
        minute: Minute (0-59)
        
    Returns:
        IST-aware datetime for today at the specified time
    """
    today = datetime.now(IST).date()
    return datetime.combine(today, time(hour, minute), tzinfo=IST)


# Export key utilities
__all__ = [
    'IST',
    'now_ist',
    'today_ist', 
    'time_now_ist',
    'make_ist_aware',
    'market_time'
]