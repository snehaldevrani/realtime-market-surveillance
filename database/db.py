"""
Database setup and management.
Creates tables, manages connections, handles cleanup.
"""

import sqlite3
import aiosqlite
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

from utils.logger import get_logger

logger = get_logger(__name__)


class Database:
    """Database manager for SQLite operations."""
    
    def __init__(self, db_path: str = "data/mug_bot.db"):
        """
        Initialize database manager.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None
        
        # Ensure data directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    
    async def connect(self):
        """Establish database connection."""
        if self.conn is None:
            self.conn = await aiosqlite.connect(self.db_path)
            self.conn.row_factory = aiosqlite.Row
            logger.info(f"Connected to database: {self.db_path}")
    
    async def disconnect(self):
        """Close database connection."""
        if self.conn:
            await self.conn.close()
            self.conn = None
            logger.info("Disconnected from database")
    
    async def setup_tables(self):
        """Create all required database tables."""
        await self.connect()
        
        # Table 1: Player bazaar snapshots (per-player inventory tracking)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS player_bazaar_snapshots (
                player_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                price INTEGER NOT NULL,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (player_id, item_id)
            )
        """)
        
        # Table 2: Tracked targets (active monitoring)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tracked_targets (
                player_id INTEGER PRIMARY KEY,
                player_name TEXT,
                accumulated_value INTEGER DEFAULT 0,
                last_action_relative TEXT,
                last_action_minutes INTEGER,
                last_action_timestamp INTEGER,
                last_action_status TEXT,
                status_state TEXT,
                status_description TEXT,
                status_details TEXT,
                first_detected TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_sale_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_alerted TIMESTAMP,
                last_alerted_value INTEGER DEFAULT 0,
                sales_breakdown TEXT,
                travel_state TEXT DEFAULT 'Okay',
                travel_last_description TEXT,
                sa_deduction_applied INTEGER DEFAULT 0,
                job_type_id INTEGER,
                job_rating INTEGER
            )
        """)
        
        # Table 3: Alert log (history of all alerts)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                player_name TEXT,
                accumulated_value INTEGER,
                last_action_minutes INTEGER,
                status_state TEXT,
                alerted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Table 4: Monitored items configuration
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS monitored_items (
                item_id INTEGER PRIMARY KEY,
                item_name TEXT NOT NULL,
                enabled BOOLEAN DEFAULT 1
            )
        """)
        
        # Table 5: Transaction log (audit trail of all sales)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS transaction_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                player_name TEXT,
                item_id INTEGER,
                item_name TEXT,
                quantity INTEGER,
                unit_price INTEGER,
                total_value INTEGER,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Add index for faster lookups
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_transaction_log_player 
            ON transaction_log(player_id)
        """)
        
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_alert_log_time 
            ON alert_log(alerted_at)
        """)
        
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_alert_log_player 
            ON alert_log(player_id)
        """)
        
        # NEW TABLE: Registered user API keys
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS registered_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_key TEXT UNIQUE NOT NULL,
                discord_id INTEGER NOT NULL,
                torn_user_id INTEGER NOT NULL,
                torn_username TEXT NOT NULL,
                profile_image TEXT,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used TIMESTAMP,
                status TEXT DEFAULT 'active'
            )
        """)
        
        # NEW TABLE: Alert channels
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_channels (
                channel_id INTEGER PRIMARY KEY,
                added_by_discord_id INTEGER NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # NEW TABLE: Filtered channel config (for low-stat targets)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS filtered_channel_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                channel_id INTEGER NOT NULL,
                status INTEGER NOT NULL,
                added_by_discord_id INTEGER NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # NEW TABLE: VIP players (persistent across restarts)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS vip_players (
                player_id INTEGER PRIMARY KEY,
                added_by_discord_id INTEGER NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # NEW TABLE: Dropped keys history
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS dropped_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_key TEXT,
                source TEXT,
                discord_id INTEGER,
                torn_user_id INTEGER,
                torn_username TEXT,
                error_code INTEGER,
                error_message TEXT,
                requests_made INTEGER,
                dropped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # NEW TABLE: Dropped targets history
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS dropped_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                player_name TEXT,
                accumulated_value INTEGER,
                drop_reason TEXT,
                dropped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # INDEX for faster lookups
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_dropped_targets_time 
            ON dropped_targets(dropped_at)
        """)
        
        # NEW INDEX: For registered keys
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_registered_keys_discord 
            ON registered_keys(discord_id)
        """)
        
        await self.conn.commit()
        logger.info("Database tables created successfully")
    
    async def cleanup_old_data(self, retention_days: int = 3):
        """
        Clean up data older than retention period.
        
        Args:
            retention_days: Number of days to keep data
        """
        await self.connect()
        
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        cutoff_str = cutoff_date.strftime('%Y-%m-%d %H:%M:%S')
        
        # Delete old alert logs
        cursor = await self.conn.execute(
            "DELETE FROM alert_log WHERE alerted_at < ?",
            (cutoff_str,)
        )
        deleted_alerts = cursor.rowcount
        
        await self.conn.commit()
        
        logger.info(f"Cleanup completed: Deleted {deleted_alerts} old alert records")
        
        return {
            'deleted_alerts': deleted_alerts,
            'cutoff_date': cutoff_str
        }
    
    async def get_database_stats(self) -> dict:
        """
        Get database statistics.
        
        Returns:
            Dict with database metrics
        """
        await self.connect()
        
        # Count records in each table
        cursor = await self.conn.execute("SELECT COUNT(*) FROM player_bazaar_snapshots")
        bazaar_count = (await cursor.fetchone())[0]
        
        cursor = await self.conn.execute("SELECT COUNT(*) FROM tracked_targets")
        tracked_count = (await cursor.fetchone())[0]
        
        cursor = await self.conn.execute("SELECT COUNT(*) FROM alert_log")
        alert_count = (await cursor.fetchone())[0]
        
        # Get database file size
        db_size_bytes = Path(self.db_path).stat().st_size
        db_size_mb = db_size_bytes / (1024 * 1024)
        
        return {
            'bazaar_records': bazaar_count,
            'tracked_targets': tracked_count,
            'total_alerts': alert_count,
            'database_size_mb': round(db_size_mb, 2),
            'database_size': f"{db_size_mb:.2f} MB"
        }
    
    async def reset_database(self):
        """
        Reset database (for testing).
        WARNING: Deletes all data!
        """
        await self.connect()
    
        tables = ['player_bazaar_snapshots', 'tracked_targets', 'alert_log', 'transaction_log']
    
        for table in tables:
            await self.conn.execute(f"DELETE FROM {table}")
    
        await self.conn.commit()
        logger.warning("Database reset - all data deleted!")
    
        async def vacuum(self):
            """Optimize database by reclaiming space."""
            await self.connect()
            await self.conn.execute("VACUUM")
            logger.info("Database vacuumed")


# Singleton instance
_db_instance: Optional[Database] = None


def get_database(db_path: str = "data/mug_bot.db") -> Database:
    """
    Get database singleton instance.
    
    Args:
        db_path: Path to database file
        
    Returns:
        Database instance
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = Database(db_path)
    return _db_instance