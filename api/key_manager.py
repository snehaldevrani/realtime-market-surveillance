"""
API key manager for Torn API.
Handles key rotation, rate limiting, and usage tracking.
Supports admin (in-memory) and registered (persistent) keys.
"""

import time
from typing import Optional, List
from database.db import get_database
from utils.logger import get_logger

logger = get_logger(__name__)


class APIKeyManager:
    """Manages pool of Torn API keys with rotation and rate limiting."""
    
    def __init__(self):
        """Initialize key manager with empty in-memory pool."""
        self.admin_keys: List[str] = []
        self.key_usage = {}
        self.current_index = 0
        self.total_requests = 0
        self.permanently_bad_keys = set()
        self.notified_dropped_keys = set()
        self.rate_limit_per_minute = 40  # Default, can be changed by admin
        
        logger.info("API Key Manager initialized")
    
    def add_admin_keys(self, api_keys: List[str]):
        """
        Add admin keys to pool and persist to database.
        
        Args:
            api_keys: List of API keys to add
        """
        added_count = 0
        for key in api_keys:
            if key not in self.admin_keys and key not in self.permanently_bad_keys:
                self.admin_keys.append(key)
                self.key_usage[key] = {
                    'count': 0,
                    'reset_time': time.time() + 60,
                    'status': 'active',
                    'rate_limited_until': 0,
                    'source': 'admin',
                    'requests_made': 0
                }
                added_count += 1
        
        # NO CONSOLE LOG - completely silent
        return added_count
    
    async def persist_admin_keys(self):
        """Save current admin keys to database so they survive restarts."""
        db = get_database()
        await db.connect()
        
        for key in self.admin_keys:
            if key not in self.permanently_bad_keys:
                try:
                    await db.conn.execute(
                        "INSERT OR IGNORE INTO admin_keys (api_key) VALUES (?)",
                        (key,)
                    )
                except Exception:
                    pass
        
        await db.conn.commit()
    
    async def load_admin_keys(self):
        """Load admin keys from database on startup."""
        db = get_database()
        await db.connect()
        
        cursor = await db.conn.execute("SELECT api_key FROM admin_keys")
        rows = await cursor.fetchall()
        
        loaded = 0
        for row in rows:
            key = row['api_key']
            if key not in self.admin_keys and key not in self.permanently_bad_keys:
                self.admin_keys.append(key)
                self.key_usage[key] = {
                    'count': 0,
                    'reset_time': time.time() + 60,
                    'status': 'active',
                    'rate_limited_until': 0,
                    'source': 'admin',
                    'requests_made': 0
                }
                loaded += 1
        
        if loaded:
            logger.info(f"✅ Loaded {loaded} admin keys from database")
    
    async def load_registered_keys(self):
        """Load active registered keys from database."""
        db = get_database()
        await db.connect()
        
        cursor = await db.conn.execute("""
            SELECT api_key, discord_id, torn_user_id, torn_username 
            FROM registered_keys 
            WHERE status = 'active'
        """)
        
        rows = await cursor.fetchall()
        
        for row in rows:
            key = row['api_key']
            if key not in self.key_usage and key not in self.permanently_bad_keys:
                self.key_usage[key] = {
                    'count': 0,
                    'reset_time': time.time() + 60,
                    'status': 'active',
                    'rate_limited_until': 0,
                    'source': 'registered',
                    'discord_id': row['discord_id'],
                    'torn_user_id': row['torn_user_id'],
                    'torn_username': row['torn_username'],
                    'requests_made': 0
                }
        
        logger.info(f"✅ Loaded {len(rows)} registered keys from database")
    
    def get_all_keys(self) -> List[str]:
        """Get combined list of admin + registered keys."""
        return list(self.key_usage.keys())
    
    def _reset_expired_counters(self):
        """Reset usage counters for keys whose minute has passed."""
        current_time = time.time()
        
        for key in self.key_usage:
            if current_time >= self.key_usage[key]['reset_time']:
                self.key_usage[key]['count'] = 0
                self.key_usage[key]['reset_time'] = current_time + 60
            
            if current_time >= self.key_usage[key]['rate_limited_until']:
                if self.key_usage[key]['status'] == 'rate_limited':
                    self.key_usage[key]['status'] = 'active'
    
    def get_available_key(self) -> Optional[str]:
        """Get next available API key for use."""
        self._reset_expired_counters()
        
        all_keys = self.get_all_keys()
        
        if not all_keys:
            logger.warning("⚠️ No API keys available (use /admin_add_apikey or /register)")
            return None
        
        current_time = time.time()
        attempts = 0
        max_attempts = len(all_keys)
        
        while attempts < max_attempts:
            key = all_keys[self.current_index % len(all_keys)]
            usage = self.key_usage[key]
            
            if usage['status'] == 'permanently_bad':
                self.current_index = (self.current_index + 1) % len(all_keys)
                attempts += 1
                continue
            
            if usage['status'] == 'rate_limited' and usage['rate_limited_until'] > current_time:
                self.current_index = (self.current_index + 1) % len(all_keys)
                attempts += 1
                continue
            
            if usage['count'] < self.rate_limit_per_minute:
                self.key_usage[key]['count'] += 1
                self.key_usage[key]['requests_made'] += 1
                self.total_requests += 1
                
                # NO CONSOLE LOG for key usage
                
                if usage['count'] >= self.rate_limit_per_minute:
                    self.current_index = (self.current_index + 1) % len(all_keys)
                
                return key
            
            self.current_index = (self.current_index + 1) % len(all_keys)
            attempts += 1
        
        logger.warning("⚠️ All API keys exhausted this cycle")
        return None
    
    def report_rate_limit(self, api_key: str):
        """Mark a key as temporarily rate limited (NO DM)."""
        if api_key in self.key_usage:
            self.key_usage[api_key]['status'] = 'rate_limited'
            self.key_usage[api_key]['rate_limited_until'] = time.time() + 60
            logger.warning(f"⏸️ API key temporarily rate limited (rotating to next)")
    
    async def report_invalid_key(self, api_key: str, error_code: int, error_msg: str, bot):
        """
        Mark a key as permanently invalid and send DMs.
        
        Args:
            api_key: The invalid key
            error_code: Torn API error code
            error_msg: Error message
            bot: Discord bot instance for sending DMs
        """
        if api_key in self.notified_dropped_keys:
            return  # Already notified, skip
    
        self.notified_dropped_keys.add(api_key)
        if api_key not in self.key_usage:
            return
        
        usage = self.key_usage[api_key]
        source = usage.get('source', 'unknown')
        requests_made = usage.get('requests_made', 0)
        
        # Mark as permanently bad
        self.key_usage[api_key]['status'] = 'permanently_bad'
        self.permanently_bad_keys.add(api_key)
        
        # Console log (NO key identifier)
        logger.error(f"❌ API key permanently dropped (Error {error_code})")
        
        # Store in database
        db = get_database()
        await db.connect()
        
        discord_id = usage.get('discord_id')
        torn_user_id = usage.get('torn_user_id')
        torn_username = usage.get('torn_username')
        
        await db.conn.execute("""
            INSERT INTO dropped_keys (full_key, source, discord_id, torn_user_id, torn_username, error_code, error_message, requests_made)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (api_key, source, discord_id, torn_user_id, torn_username, error_code, error_msg, requests_made))
        await db.conn.commit()
        
        # Send DMs
        import os
        admin_id = int(os.getenv("ADMIN_DISCORD_ID", 0))
        
        if bot:
            # DM admin (always)
            await self._dm_admin_key_dropped(bot, admin_id, api_key, source, error_code, error_msg, requests_made, discord_id, torn_user_id, torn_username)
            
            # DM user (if registered key)
            if source == 'registered' and discord_id:
                await self._dm_user_key_dropped(bot, discord_id, torn_username, torn_user_id, error_code, error_msg, requests_made)
        
        # Remove from admin keys list if admin key
        if api_key in self.admin_keys:
            self.admin_keys.remove(api_key)
            # Also remove from persistent DB
            await db.conn.execute("DELETE FROM admin_keys WHERE api_key = ?", (api_key,))
            await db.conn.commit()
        
        # Delete from DB if registered (auto-unregister)
        if source == 'registered':
            await db.conn.execute("DELETE FROM registered_keys WHERE api_key = ?", (api_key,))
            await db.conn.commit()
    
    async def _dm_admin_key_dropped(self, bot, admin_id, key, source, error_code, error_msg, requests, discord_id, torn_id, torn_name):
        """Send DM to admin when any key drops."""
        try:
            admin_user = await bot.fetch_user(admin_id)
            
            if source == 'admin':
                message = f"""❌ **Admin API Key Dropped**

**Full Key:** `{key}`
**Error Code:** {error_code}
**Reason:** {error_msg}
**Requests Made:** {requests:,}
**Dropped At:** <t:{int(time.time())}:F>

This key has been permanently removed from rotation."""
            
            else:  # registered
                message = f"""❌ **Registered API Key Dropped**

**Discord User:** <@{discord_id}>
**Torn Account:** {torn_name} (#{torn_id})
**Error Code:** {error_code}
**Reason:** {error_msg}
**Requests Made:** {requests:,}
**Dropped At:** <t:{int(time.time())}:F>

⚠️ Key has been automatically unregistered. User has been notified to re-register."""
            
            await admin_user.send(message)
        
        except Exception as e:
            logger.error(f"Failed to DM admin about dropped key: {e}")
    
    async def _dm_user_key_dropped(self, bot, user_id, torn_name, torn_id, error_code, error_msg, requests):
        """Send DM to user when their registered key drops."""
        try:
            user = await bot.fetch_user(user_id)
            
            message = f"""❌ **Your Torn API Key Has Been Dropped**

**Torn Account:** {torn_name} (#{torn_id})
**Error Code:** {error_code}
**Reason:** {error_msg}
**Requests Made:** {requests:,}
**Dropped At:** <t:{int(time.time())}:F>

Your key is no longer active in the bot. If you'd like to continue contributing, please register a new key using:

`/register api_key:<your_new_key>`

Thank you for supporting the mug bot! 🎯"""
            
            await user.send(message)
        
        except Exception as e:
            logger.error(f"Failed to DM user {user_id} about dropped key: {e}")
    
    def get_stats(self) -> dict:
        """Get current usage statistics."""
        current_time = time.time()
        
        active_count = 0
        rate_limited_count = 0
        bad_count = len(self.permanently_bad_keys)
        
        admin_count = len([k for k in self.admin_keys if k in self.key_usage and self.key_usage[k]['status'] != 'permanently_bad'])
        registered_count = len([k for k, v in self.key_usage.items() if v.get('source') == 'registered' and v['status'] != 'permanently_bad'])
        
        for key in self.key_usage:
            usage = self.key_usage[key]
            
            if usage['status'] == 'permanently_bad':
                continue
            elif usage['status'] == 'rate_limited' and usage['rate_limited_until'] > current_time:
                rate_limited_count += 1
            elif usage['count'] < 40:
                active_count += 1
        
        return {
            'total_keys': len(self.key_usage) - bad_count,
            'admin_keys': admin_count,
            'registered_keys': registered_count,
            'active': active_count,
            'rate_limited': rate_limited_count,
            'permanently_bad': bad_count,
            'total_requests': self.total_requests
        }
        
    def set_rate_limit(self, limit: int) -> bool:
        """
        Set the rate limit per minute for all keys.
    
        Args:
            limit: Number of requests per minute (10-60)
        
        Returns:
            True if successful, False if invalid
        """
        if limit < 10 or limit > 60:
            return False
    
        self.rate_limit_per_minute = limit
        logger.info(f"⚙️ Rate limit changed to {limit} requests/minute")
        return True


# Global instance
_key_manager: Optional[APIKeyManager] = None


def init_key_manager():
    """Initialize global key manager (empty pool)."""
    global _key_manager
    _key_manager = APIKeyManager()


def get_key_manager() -> APIKeyManager:
    """Get global key manager instance."""
    if _key_manager is None:
        raise RuntimeError("Key manager not initialized. Call init_key_manager() first.")
    return _key_manager