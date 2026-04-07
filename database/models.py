"""
Database models and CRUD operations.
Handles all database queries for bazaar state, tracking, and alerts.
"""

import json
from typing import List, Dict, Optional
from datetime import datetime

from database.db import get_database
from utils.logger import get_logger

from datetime import datetime


logger = get_logger(__name__)


class BazaarStateModel:
    """Operations for player_bazaar_snapshots table."""
    
    def __init__(self):
        self.db = get_database()
    
    async def save_player_snapshot(self, player_id: int, bazaar_items: List[dict]):
        """
        Save current bazaar snapshot for a player (replaces old data).
        
        Args:
            player_id: Player's Torn ID
            bazaar_items: List of items [{item_id, quantity, price}]
        """
        await self.db.connect()
        
        # Delete old snapshot for this player
        await self.db.conn.execute(
            "DELETE FROM player_bazaar_snapshots WHERE player_id = ?",
            (player_id,)
        )
        
        # Insert new snapshot
        for item in bazaar_items:
            await self.db.conn.execute("""
                INSERT OR REPLACE INTO player_bazaar_snapshots 
                (player_id, item_id, quantity, price, last_updated)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                player_id,
                item['item_id'],
                item['quantity'],
                item['price']
            ))
        
        await self.db.conn.commit()
        logger.debug(f"Saved bazaar snapshot for player {player_id}: {len(bazaar_items)} items")
    
    async def get_player_snapshot(self, player_id: int) -> dict:
        """
        Get previous bazaar snapshot for a player.
        
        Args:
            player_id: Player's Torn ID
            
        Returns:
            Dict of {item_id: {'quantity': int, 'price': int}}
        """
        await self.db.connect()
        
        cursor = await self.db.conn.execute("""
            SELECT item_id, quantity, price
            FROM player_bazaar_snapshots
            WHERE player_id = ?
        """, (player_id,))
        
        rows = await cursor.fetchall()
        
        snapshot = {}
        for row in rows:
            snapshot[row['item_id']] = {
                'quantity': row['quantity'],
                'price': row['price']
            }
        
        return snapshot


class TrackedTargetsModel:
    """Operations for tracked_targets table."""
    
    def __init__(self):
        self.db = get_database()
    
    async def add_or_update_target(self, player_id: int, player_name: str, value_to_add: int, 
                                   item_id: int, profile_data: Optional[dict] = None):
        """
        Add new target or update existing one with new sale value.
        
        Args:
            player_id: Player's Torn ID
            player_name: Player's name
            value_to_add: Sale value to add to accumulated
            item_id: Item ID that was sold
            profile_data: Optional profile data if already fetched
        """
        await self.db.connect()
        
        # Check if target exists
        cursor = await self.db.conn.execute(
            "SELECT accumulated_value, sales_breakdown FROM tracked_targets WHERE player_id = ?",
            (player_id,)
        )
        existing = await cursor.fetchone()
        
        if existing:
            # Update existing target
            new_accumulated = existing['accumulated_value'] + value_to_add
            
            # Update sales breakdown
            sales_breakdown = json.loads(existing['sales_breakdown']) if existing['sales_breakdown'] else {}
            sales_breakdown[str(item_id)] = sales_breakdown.get(str(item_id), 0) + value_to_add
            
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            await self.db.conn.execute("""
                UPDATE tracked_targets
                SET accumulated_value = ?,
                    sales_breakdown = ?,
                    player_name = ?,
                    last_sale_time = ?
                WHERE player_id = ?
            """, (new_accumulated, json.dumps(sales_breakdown), player_name, current_time, player_id))
            
            logger.debug(f"Updated target {player_name} ({player_id}): +${value_to_add:,} = ${new_accumulated:,}")
        
        else:
            # Insert new target
            sales_breakdown = {str(item_id): value_to_add}
            
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            await self.db.conn.execute("""
                INSERT INTO tracked_targets (
                    player_id, player_name, accumulated_value, sales_breakdown,
                    first_detected, last_sale_time, last_alerted_value
                )
                VALUES (?, ?, ?, ?, ?, ?, 0)
            """, (player_id, player_name, value_to_add, json.dumps(sales_breakdown), current_time, current_time))
            
            logger.info(f"New target added: {player_name} ({player_id}) with ${value_to_add:,}")
        
        await self.db.conn.commit()
    
    async def update_profile_data(self, player_id: int, profile_data: dict):
        """
        Update target with profile information.
        
        Args:
            player_id: Player's Torn ID
            profile_data: Parsed profile data from API
        """
        await self.db.connect()
        
        await self.db.conn.execute("""
            UPDATE tracked_targets
            SET last_action_relative = ?,
                last_action_minutes = ?,
                last_action_timestamp = ?,
                last_action_status = ?,
                status_state = ?,
                status_description = ?,
                travel_state = ?,
                travel_last_description = ?
            WHERE player_id = ?
        """, (
            profile_data['last_action_relative'],
            profile_data['last_action_minutes'],
            profile_data['last_action_timestamp'],
            profile_data['last_action_status'],
            profile_data['status_state'],
            profile_data['status_description'],
            profile_data['status_state'],  # travel_state
            profile_data['status_description'],  # travel_last_description
            player_id
        ))
        
        await self.db.conn.commit()
        logger.debug(f"Updated profile data for player {player_id}")
        
    async def update_accumulated_and_travel(self, player_id: int, new_accumulated: int, 
                                            sa_deduction_applied: bool = None):
        """
        Update target's accumulated value and optionally SA deduction flag.

        Args:
            player_id: Player's Torn ID
            new_accumulated: New accumulated value (can be negative)
            sa_deduction_applied: Whether SA deduction was applied
        """
        await self.db.connect()
    
        if sa_deduction_applied is not None:
            await self.db.conn.execute("""
                UPDATE tracked_targets
                SET accumulated_value = ?,
                    sa_deduction_applied = ?
                WHERE player_id = ?
            """, (new_accumulated, 1 if sa_deduction_applied else 0, player_id))
        else:
            await self.db.conn.execute("""
                UPDATE tracked_targets
                SET accumulated_value = ?
                WHERE player_id = ?
            """, (new_accumulated, player_id))

        await self.db.conn.commit()
        logger.debug(f"Updated accumulated value for player {player_id}: ${new_accumulated:,}")
        
    async def reset_sa_deduction(self, player_id: int):
        """
        Reset South Africa deduction flag (when player returns to Torn).
    
        Args:
            player_id: Player's Torn ID
        """
        await self.db.connect()
    
        await self.db.conn.execute("""
            UPDATE tracked_targets
            SET sa_deduction_applied = 0
            WHERE player_id = ?
        """, (player_id,))
    
        await self.db.conn.commit()
        logger.debug(f"Reset SA deduction flag for player {player_id}")
    
    async def reset_target(self, player_id: int):
        """
        Reset target's accumulated value (when they come online).
        
        Args:
            player_id: Player's Torn ID
        """
        await self.db.connect()
        
        # Delete the target record
        await self.db.conn.execute(
            "DELETE FROM tracked_targets WHERE player_id = ?",
            (player_id,)
        )
        
        await self.db.conn.commit()
        logger.info(f"Reset target {player_id} (came online)")
    
    async def get_target(self, player_id: int) -> Optional[dict]:
        """
        Get single target by player ID.
        
        Args:
            player_id: Player's Torn ID
            
        Returns:
            Target dict or None
        """
        await self.db.connect()
        
        cursor = await self.db.conn.execute(
            "SELECT * FROM tracked_targets WHERE player_id = ?",
            (player_id,)
        )
        
        row = await cursor.fetchone()
        
        if not row:
            return None
        
        return dict(row)
    
    async def get_targets_for_alerts(self, min_accumulated: int, min_inactivity: int) -> List[dict]:
        """
        Get all targets that meet alert criteria AND haven't been alerted at current value.
        Applies Clothing Store protection multiplier.
    
        Args:
            min_accumulated: Minimum accumulated value
            min_inactivity: Minimum inactivity in minutes

        Returns:
            List of target dicts
        """
        await self.db.connect()
    
        cursor = await self.db.conn.execute("""
            SELECT * FROM tracked_targets
            WHERE status_state = 'Okay'
            AND last_action_minutes >= ?
            AND (last_alerted_value IS NULL OR accumulated_value > last_alerted_value)
            ORDER BY accumulated_value DESC
        """, (min_inactivity,))
    
        rows = await cursor.fetchall()
        targets = []

        # Filter based on effective accumulated value (with protection multiplier)
        for row in rows:
            target = dict(row)
            accumulated = target['accumulated_value']
            
            # Check for Clothing Store protection
            job_type_id = target.get('job_type_id')
            job_rating = target.get('job_rating')
            has_protection = (job_type_id == 5 and job_rating is not None and job_rating >= 7)
            
            if has_protection:
                effective_accumulated = int(accumulated * 0.25)
            else:
                effective_accumulated = accumulated
            
            # Only include if effective value meets threshold
            if effective_accumulated >= min_accumulated:
                targets.append(target)

        return targets
    
    async def get_all_targets(self) -> List[dict]:
        """
        Get all tracked targets.
        
        Returns:
            List of all target dicts
        """
        await self.db.connect()
        
        cursor = await self.db.conn.execute(
            "SELECT * FROM tracked_targets ORDER BY accumulated_value DESC"
        )
        
        rows = await cursor.fetchall()
        
        return [dict(row) for row in rows]
    
    async def update_last_alerted(self, player_id: int, alerted_value: int):
        """
        Update last_alerted timestamp and value.
        
        Args:
            player_id: Player's Torn ID
            alerted_value: The accumulated value that was alerted
        """
        await self.db.connect()
        
        await self.db.conn.execute("""
            UPDATE tracked_targets
            SET last_alerted = CURRENT_TIMESTAMP,
                last_alerted_value = ?
            WHERE player_id = ?
        """, (alerted_value, player_id))
        
        await self.db.conn.commit()
        logger.debug(f"Updated last_alerted for player {player_id} with value ${alerted_value:,}")
        
    async def batch_update_profile_data(self, updates: List[dict]):
        """
        Batch update profile data for multiple targets.
    
        Args:
            updates: List of dicts with player_id and profile data
        """
        if not updates:
            return
    
        await self.db.connect()
    
        # Prepare batch data
        batch_data = []
        for update in updates:
            player_id = update['player_id']
            profile_data = update['profile_data']
        
            batch_data.append((
                profile_data['last_action_relative'],
                profile_data['last_action_minutes'],
                profile_data['last_action_timestamp'],
                profile_data['last_action_status'],
                profile_data['status_state'],
                profile_data['status_description'],
                profile_data.get('status_details', ''),  # ADD THIS
                profile_data['status_state'],  # travel_state
                profile_data['status_description'],  # travel_last_description
                profile_data.get('job_type_id'),  # ADD THIS
                profile_data.get('job_rating'),  # ADD THIS
                player_id
            ))
    
        # Execute batch update
        await self.db.conn.executemany("""
            UPDATE tracked_targets
            SET last_action_relative = ?,
                last_action_minutes = ?,
                last_action_timestamp = ?,
                last_action_status = ?,
                status_state = ?,
                status_description = ?,
                status_details = ?,
                travel_state = ?,
                travel_last_description = ?,
                job_type_id = ?,
                job_rating = ?
            WHERE player_id = ?
        """, batch_data)
    
        await self.db.conn.commit()
        logger.debug(f"✅ Batch updated {len(updates)} target profiles")
        
    async def get_watch_list_players(self) -> set:
        """
        Get set of all player IDs currently on watch list.
    
        Returns:
            Set of player_ids
        """
        await self.db.connect()
    
        cursor = await self.db.conn.execute(
            "SELECT player_id FROM tracked_targets"
        )
    
        rows = await cursor.fetchall()
    
        return {row['player_id'] for row in rows}


class AlertLogModel:
    """Operations for alert_log table."""
    
    def __init__(self):
        self.db = get_database()
    
    async def log_alert(self, player_id: int, player_name: str, accumulated_value: int,
                       last_action_minutes: int, status_state: str):
        """
        Log an alert to history.
        
        Args:
            player_id: Player's Torn ID
            player_name: Player's name
            accumulated_value: Cash amount
            last_action_minutes: Inactivity in minutes
            status_state: Player status
        """
        await self.db.connect()
        
        await self.db.conn.execute("""
            INSERT INTO alert_log (
                player_id, player_name, accumulated_value,
                last_action_minutes, status_state, alerted_at
            )
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (player_id, player_name, accumulated_value, last_action_minutes, status_state))
        
        await self.db.conn.commit()
        logger.debug(f"Logged alert for {player_name} ({player_id})")
    
    async def get_recent_alerts(self, limit: int = 10) -> List[dict]:
        """
        Get recent alerts.
        
        Args:
            limit: Maximum number of alerts to return
            
        Returns:
            List of alert dicts
        """
        await self.db.connect()
        
        cursor = await self.db.conn.execute("""
            SELECT * FROM alert_log
            ORDER BY alerted_at DESC
            LIMIT ?
        """, (limit,))
        
        rows = await cursor.fetchall()
        
        return [dict(row) for row in rows]
    
    async def get_alerts_24h(self) -> int:
        """
        Get count of alerts in last 24 hours.
        
        Returns:
            Count of alerts
        """
        await self.db.connect()
        
        cursor = await self.db.conn.execute("""
            SELECT COUNT(*) as count FROM alert_log
            WHERE alerted_at >= datetime('now', '-1 day')
        """)
        
        row = await cursor.fetchone()
        return row['count'] if row else 0


class MonitoredItemsModel:
    """Operations for monitored_items table."""
    
    def __init__(self):
        self.db = get_database()
    
    async def add_item(self, item_id: int, item_name: str):
        """
        Add item to monitoring.
        
        Args:
            item_id: Item ID
            item_name: Item name
        """
        await self.db.connect()
        
        await self.db.conn.execute("""
            INSERT OR REPLACE INTO monitored_items (item_id, item_name, enabled)
            VALUES (?, ?, 1)
        """, (item_id, item_name))
        
        await self.db.conn.commit()
        logger.info(f"Added monitored item: {item_name} ({item_id})")
    
    async def get_enabled_items(self) -> List[dict]:
        """
        Get all enabled monitored items.
        
        Returns:
            List of item dicts
        """
        await self.db.connect()
        
        cursor = await self.db.conn.execute("""
            SELECT * FROM monitored_items
            WHERE enabled = 1
        """)
        
        rows = await cursor.fetchall()
        
        return [dict(row) for row in rows]
    
    async def get_item_names_map(self) -> Dict[int, str]:
        """
        Get mapping of item_id to item_name.
        
        Returns:
            Dict of {item_id: item_name}
        """
        await self.db.connect()
        
        cursor = await self.db.conn.execute("SELECT item_id, item_name FROM monitored_items")
        rows = await cursor.fetchall()
        
        return {row['item_id']: row['item_name'] for row in rows}

class TransactionLogModel:
    """Log every individual sale for audit trail."""
    
    def __init__(self):
        self.db = get_database()
    
    async def log_transaction(self, player_id: int, player_name: str, 
                             item_id: int, item_name: str, 
                             quantity: int, unit_price: int, total_value: int):
        """Log a single sale transaction."""
        await self.db.connect()
        
        await self.db.conn.execute("""
            INSERT INTO transaction_log (
                player_id, player_name, item_id, item_name,
                quantity, unit_price, total_value, detected_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (player_id, player_name, item_id, item_name, 
              quantity, unit_price, total_value))
        
        await self.db.conn.commit()
    
    async def get_player_transactions(self, player_id: int):
        """Get all transactions for a player."""
        await self.db.connect()
        
        cursor = await self.db.conn.execute("""
            SELECT * FROM transaction_log
            WHERE player_id = ?
            ORDER BY detected_at ASC
        """, (player_id,))
        
        return [dict(row) for row in await cursor.fetchall()]
    
class DroppedTargetsModel:
    """Log dropped targets for admin review."""
    
    def __init__(self):
        self.db = get_database()
    
    async def log_drop(self, player_id: int, player_name: str, 
                      accumulated_value: int, drop_reason: str):
        """Log a target being dropped."""
        await self.db.connect()
        
        await self.db.conn.execute("""
            INSERT INTO dropped_targets (
                player_id, player_name, accumulated_value, drop_reason, dropped_at
            )
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (player_id, player_name, accumulated_value, drop_reason))
        
        await self.db.conn.commit()
    
    async def get_recent_drops(self, limit: int = 10, hours: int = 8):
        """Get recently dropped targets within specified hours."""
        await self.db.connect()
        
        cursor = await self.db.conn.execute("""
            SELECT * FROM dropped_targets
            WHERE dropped_at >= datetime('now', '-' || ? || ' hours')
            ORDER BY dropped_at DESC
            LIMIT ?
        """, (hours, limit))
        
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    
    async def cleanup_old_drops(self, hours: int = 8):
        """Clean up drops older than specified hours."""
        await self.db.connect()
        
        cursor = await self.db.conn.execute("""
            DELETE FROM dropped_targets
            WHERE dropped_at < datetime('now', '-' || ? || ' hours')
        """, (hours,))
        
        await self.db.conn.commit()
        return cursor.rowcount
    
class VIPPlayersModel:
    """Manage VIP players (always monitored, never dropped)."""
    
    def __init__(self):
        self.db = get_database()
    
    async def add_vip(self, player_id: int, added_by_discord_id: int):
        """Add a player to VIP list."""
        await self.db.connect()
        
        try:
            await self.db.conn.execute("""
                INSERT INTO vip_players (player_id, added_by_discord_id)
                VALUES (?, ?)
            """, (player_id, added_by_discord_id))
            await self.db.conn.commit()
            return True
        except Exception as e:
            # Already exists
            return False
    
    async def remove_vip(self, player_id: int):
        """Remove a player from VIP list."""
        await self.db.connect()
        
        cursor = await self.db.conn.execute(
            "DELETE FROM vip_players WHERE player_id = ?",
            (player_id,)
        )
        await self.db.conn.commit()
        return cursor.rowcount > 0
    
    async def get_all_vips(self) -> set:
        """Get set of all VIP player IDs."""
        await self.db.connect()
        
        cursor = await self.db.conn.execute("SELECT player_id FROM vip_players")
        rows = await cursor.fetchall()
        return {row['player_id'] for row in rows}
    
    async def is_vip(self, player_id: int) -> bool:
        """Check if player is VIP."""
        await self.db.connect()
        
        cursor = await self.db.conn.execute(
            "SELECT 1 FROM vip_players WHERE player_id = ?",
            (player_id,)
        )
        return await cursor.fetchone() is not None