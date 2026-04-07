"""
Parsing utilities for Torn API responses.
Handles parsing of last_action times, status states, etc.
"""

import re
from datetime import datetime, timedelta
from typing import Optional, Tuple


def parse_last_action_minutes(relative: str) -> Optional[int]:
    """
    Parse last_action.relative field to extract minutes.
    
    Examples:
        "Now" -> 0
        "1 minute ago" -> 1
        "15 minutes ago" -> 15
        "2 hours ago" -> 120
        "1 day ago" -> 1440
        
    Args:
        relative: The relative time string from API
        
    Returns:
        Number of minutes, or None if parsing fails
    """
    if not relative:
        return None
    
    relative = relative.lower().strip()
    
    # Handle "now" or "online"
    if relative in ["now", "online", "just now"]:
        return 0
    
    # Extract number and unit
    match = re.match(r'(\d+)\s*(second|minute|hour|day|week|month|year)s?\s*ago', relative)
    
    if not match:
        return None
    
    number = int(match.group(1))
    unit = match.group(2)
    
    # Convert to minutes
    conversions = {
        'second': number / 60,
        'minute': number,
        'hour': number * 60,
        'day': number * 1440,
        'week': number * 10080,
        'month': number * 43200,  # Approximate
        'year': number * 525600   # Approximate
    }
    
    minutes = conversions.get(unit)
    
    return int(minutes) if minutes is not None else None


def is_player_online(relative: str) -> bool:
    """
    Check if player is considered "online" based on last_action.
    
    Online means: "Now", "1 minute ago", or any activity < 2 minutes
    
    Args:
        relative: The relative time string from API
        
    Returns:
        True if player is online, False otherwise
    """
    minutes = parse_last_action_minutes(relative)
    
    if minutes is None:
        return False
    
    # Consider online if active within last minute
    return minutes < 2


def parse_status(status_data: dict) -> Tuple[str, str, str, bool]:
    """
    Parse status information from profile API.
    
    Args:
        status_data: Status dict from API response
        
    Returns:
        Tuple of (state, description, details, is_vulnerable)
    """
    if not status_data:
        return "Unknown", "Unknown status", "", False
    
    state = status_data.get('state', 'Unknown')
    description = status_data.get('description', 'No description')
    details = status_data.get('details', '')  # ADD THIS
    
    # Determine vulnerability
    is_alertable = (state == "Okay")
    
    return state, description, details, is_alertable  # RETURN 4 values now

def is_player_mugged(status_data: dict) -> Tuple[bool, str]:
    """
    Check if player was recently mugged.
    
    Returns:
        (is_mugged: bool, mugger_name: str)
    """
    if not status_data:
        return False, ""
    
    details = status_data.get('details', '')
    
    # Check if details contains "Mugged by"
    if details and 'Mugged by' in details:
        # Extract mugger name (after "Mugged by ")
        mugger = details.replace('Mugged by ', '').strip()
        return True, mugger
    
    return False, ""


def parse_profile_response(profile_data: dict, job_data: dict = None) -> dict:
    """
    Parse complete profile API response into usable format.
    
    Args:
        profile_data: Profile dict from API response
        
    Returns:
        Parsed dict with cleaned data
    """
    if not profile_data:
        return None
    
    last_action = profile_data.get('last_action', {})
    status = profile_data.get('status', {})
    
    relative = last_action.get('relative', 'Unknown')
    minutes = parse_last_action_minutes(relative)
    is_online = is_player_online(relative)
    last_action_status = last_action.get('status', 'Unknown')  
    
    state, description, details, is_vulnerable = parse_status(status)  # CHANGE THIS LINE
    is_mugged, mugger_name = is_player_mugged(status)  # ADD THIS
    
    # Parse job data for mug protection check
    job_type_id = None
    job_rating = None
    if job_data:
        job_type_id = job_data.get('type_id')
        job_rating = job_data.get('rating')
    
    return {
        'player_id': profile_data.get('id'),
        'player_name': profile_data.get('name'),
        'last_action_relative': relative,
        'last_action_minutes': minutes,
        'last_action_timestamp': last_action.get('timestamp'),
        'last_action_status': last_action_status,  
        'is_online': is_online,
        'status_state': state,
        'status_description': description,
        'status_details': details,  # ADD THIS LINE
        'is_vulnerable': is_vulnerable,
        'level': profile_data.get('level'),
        'faction_id': profile_data.get('faction_id'),
        'is_mugged': is_mugged,
        'mugged_by': mugger_name,
        'job_type_id': job_type_id,
        'job_rating': job_rating,
    }


def calculate_time_ago(minutes: int) -> str:
    """
    Convert minutes to human-readable time string.
    
    Args:
        minutes: Number of minutes
        
    Returns:
        Formatted string like "5 minutes ago", "2 hours ago"
    """
    if minutes == 0:
        return "Now"
    elif minutes == 1:
        return "1 minute ago"
    elif minutes < 60:
        return f"{minutes} minutes ago"
    elif minutes < 120:
        return "1 hour ago"
    elif minutes < 1440:
        hours = minutes // 60
        return f"{hours} hours ago"
    elif minutes < 2880:
        return "1 day ago"
    else:
        days = minutes // 1440
        return f"{days} days ago"

