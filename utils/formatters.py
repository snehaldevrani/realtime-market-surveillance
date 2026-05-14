"""
Formatting utilities for Discord messages and data display.
Handles currency formatting, timestamps, and message generation.
"""

import json
from typing import Dict, Optional
from datetime import datetime
from datetime import datetime, timezone


def format_currency(amount: int) -> str:
    """
    Format currency amount to readable string.
    
    Examples:
        1000000 -> "$1.0M"
        15500000 -> "$15.5M"
        500000 -> "$500k"
        1500 -> "$1,500"
        
    Args:
        amount: Dollar amount as integer
        
    Returns:
        Formatted string
    """
    if amount >= 1_000_000_000:  # Billions
        value = amount / 1_000_000_000
        return f"${value:.1f}B".rstrip('0').rstrip('.')
    elif amount >= 1_000_000:  # Millions
        value = amount / 1_000_000
        return f"${value:.1f}M".rstrip('0').rstrip('.')
    elif amount >= 1_000:  # Thousands
        value = amount / 1_000
        return f"${value:.1f}k".rstrip('0').rstrip('.')
    else:
        return f"${amount:,}"


def format_currency_full(amount: int) -> str:
    """
    Format currency amount with full precision and commas.
    
    Example:
        15500000 -> "$15,500,000"
        
    Args:
        amount: Dollar amount as integer
        
    Returns:
        Formatted string with commas
    """
    return f"${amount:,}"


def format_timestamp(timestamp: Optional[int] = None) -> str:
    """
    Format Unix timestamp to readable datetime string.
    
    Args:
        timestamp: Unix timestamp (None = current time)
        
    Returns:
        Formatted datetime string
    """
    if timestamp is None:
        dt = datetime.now()
    else:
        dt = datetime.fromtimestamp(timestamp)
    
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def format_time_short(timestamp: Optional[int] = None) -> str:
    """
    Format Unix timestamp to short time string.
    
    Args:
        timestamp: Unix timestamp (None = current time)
        
    Returns:
        Formatted time string (HH:MM:SS)
    """
    if timestamp is None:
        dt = datetime.now()
    else:
        dt = datetime.fromtimestamp(timestamp)
    
    return dt.strftime('%H:%M:%S')


def format_sales_breakdown(sales_breakdown: Dict[int, int], item_names: Dict[int, str]) -> str:
    """
    Format sales breakdown by item into readable string.
    
    Args:
        sales_breakdown: Dict of {item_id: value}
        item_names: Dict of {item_id: name}
        
    Returns:
        Formatted string with breakdown
        
    Example:
        "Xanax: $8.0M | Drug Pack: $5.0M"
    """
    if not sales_breakdown:
        return "Unknown"
    
    parts = []
    for item_id, value in sales_breakdown.items():
        # Convert item_id to int if it's a string
        item_id_int = int(item_id) if isinstance(item_id, str) else item_id
        item_name = item_names.get(item_id_int, f"Item {item_id_int}")
        parts.append(f"{item_name}: {format_currency(value)}")
    
    return " | ".join(parts)


def format_status_note(status_state: str, config: dict) -> Optional[str]:
    """
    Get status note based on player state.
    
    Args:
        status_state: Player's status state
        config: Config dict with status_handling settings
        
    Returns:
        Note string or None
    """
    status_notes = {
        "Traveling": config.get('traveling_note', '🛫 Traveling - Mug when lands'),
        "Jail": config.get('jail_note', '⛓️ In Jail - Mug when busted'),
        "Hospital": config.get('hospital_note', '🏥 In Hospital')
    }
    
    return status_notes.get(status_state)


def format_alert_embed_data(target: dict, config: dict, item_names: Dict[int, str]) -> dict:
    """
    Format target data into Discord embed structure.
    
    Args:
        target: Target dict with player data
        config: Config dict
        item_names: Item ID to name mapping
        
    Returns:
        Dict with embed data
    """
    player_id = target['player_id']
    player_name = target['player_name']
    accumulated = target['accumulated_value']
    minutes = target['last_action_minutes']
    status_state = target['status_state']
    
    # Parse sales_breakdown if it's a JSON string from database
    sales_breakdown = target.get('sales_breakdown', {})
    if isinstance(sales_breakdown, str):
        try:
            sales_breakdown = json.loads(sales_breakdown)
        except (json.JSONDecodeError, TypeError):
            sales_breakdown = {}
    
    # Format basic info
    title = f"🎯 MUG TARGET"
    
    # Player info
    player_info = f"**{player_name}** ([{player_id}](https://www.torn.com/profiles.php?XID={player_id}))"
    
    # Cash amount
    cash_short = format_currency(accumulated)
    cash_full = format_currency_full(accumulated)
    
    # Calculate max potential mug (10% of cash on hand)
    # If player has Clothing Store protection, calculate from protected amount
    job_type_id = target.get('job_type_id')
    job_rating = target.get('job_rating')
    has_protection = (job_type_id == 5 and job_rating is not None and job_rating >= 7)

    if has_protection:
        # 75% protection = only 25% muggable, then 10% of that
        effective_muggable = int(accumulated * 0.25)
        max_mug = int(effective_muggable * 0.10)
    else:
        max_mug = int(accumulated * 0.10)

    max_mug_formatted = format_currency(max_mug)
    max_mug_full = format_currency_full(max_mug)
    
    # Time info
    if minutes == 0:
        time_info = "Now (Just went offline)"
    elif minutes == 1:
        time_info = "1 minute ago"
    else:
        time_info = f"{minutes} minutes ago"
    
    # Format last action status with emoji
    last_action_status = target.get('last_action_status', 'Unknown')
    if last_action_status == 'Online':
        status_display_text = "🟢 Online"
    elif last_action_status == 'Idle':
        status_display_text = "🟡 Idle"
    elif last_action_status == 'Offline':
        status_display_text = "⚪ Offline"
    else:
        status_display_text = f"⚪ {last_action_status}"
    
    # Status note
    status_note = format_status_note(status_state, config.get('status_handling', {}))
    if status_note:
        status_display = f"{status_state} - {status_note}"
    else:
        status_display = status_state
    
    # Attack link
    attack_url = f"https://www.torn.com/page.php?sid=attack&user2ID={player_id}"
    
    # Sales breakdown
    if sales_breakdown:
        breakdown_str = format_sales_breakdown(sales_breakdown, item_names)
    else:
        breakdown_str = None
    
    return {
        'title': title,
        'player_info': player_info,
        'player_id': player_id,
        'player_name': player_name,
        'cash_short': cash_short,
        'cash_full': cash_full,
        'max_mug_short': max_mug_formatted,  # ADD THIS
        'max_mug_full': max_mug_full,
        'time_info': time_info,
        'status_display_text': status_display_text,
        'minutes': minutes,
        'status_display': status_display,
        'status_state': status_state,
        'attack_url': attack_url,
        'breakdown': breakdown_str,
        'timestamp': datetime.now(timezone.utc)  # ← Explicitly UTC (TCT)
    }


def format_stats_message(stats: dict) -> str:
    """
    Format statistics into readable message.
    
    Args:
        stats: Stats dict with various metrics
        
    Returns:
        Formatted stats string
    """
    lines = []
    lines.append("📊 **Bot Statistics**")
    lines.append("")
    
    if 'uptime' in stats:
        lines.append(f"⏱️ Uptime: {stats['uptime']}")
    
    if 'items_monitored' in stats:
        lines.append(f"📦 Items Monitored: {stats['items_monitored']}")
    
    if 'active_targets' in stats:
        lines.append(f"🎯 Active Targets: {stats['active_targets']}")
    
    if 'alerts_sent_24h' in stats:
        lines.append(f"🚨 Alerts (24h): {stats['alerts_sent_24h']}")
    
    if 'total_value_tracked' in stats:
        lines.append(f"💰 Total Value Tracked: {format_currency(stats['total_value_tracked'])}")
    
    if 'api_calls_minute' in stats:
        lines.append(f"🔑 API Calls/min: {stats['api_calls_minute']}")
    
    if 'database_size' in stats:
        lines.append(f"💾 Database Size: {stats['database_size']}")
    
    return "\n".join(lines)


def format_recent_alerts(alerts: list, limit: int = 10) -> str:
    """
    Format recent alerts into readable list.
    
    Args:
        alerts: List of alert dicts
        limit: Maximum number to show
        
    Returns:
        Formatted string
    """
    if not alerts:
        return "No recent alerts found."
    
    lines = []
    lines.append(f"📋 **Recent Alerts (Last {min(len(alerts), limit)})**")
    lines.append("")
    
    for i, alert in enumerate(alerts[:limit], 1):
        player_name = alert.get('player_name', 'Unknown')
        player_id = alert.get('player_id', 0)
        value = alert.get('accumulated_value', 0)
        minutes = alert.get('last_action_minutes', 0)
        timestamp = alert.get('alerted_at', '')
        
        lines.append(f"**{i}.** {player_name} ({player_id})")
        lines.append(f"   💰 {format_currency(value)} | ⏱️ {minutes}m ago | 🕐 {timestamp}")
        lines.append("")
    
    return "\n".join(lines)