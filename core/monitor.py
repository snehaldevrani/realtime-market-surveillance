"""
Main monitoring loop.
Orchestrates the detection, tracking, and alerting cycle.
"""

import asyncio
from typing import Optional
from datetime import datetime

import random  # For random delays
from database.models import MonitoredItemsModel
from core.detector import get_detector
from core.tracker import get_tracker
from core.alerter import get_alerter
from utils.logger import get_logger

logger = get_logger(__name__)


class Monitor:
    """Main monitoring orchestrator."""
    
    def __init__(self, config: dict):
        """
        Initialize monitor.
        
        Args:
            config: Configuration dict
        """
        self.config = config
        self.check_interval = config.get('monitoring', {}).get('check_interval', 15)
        self.min_accumulated = config.get('alerts', {}).get('min_accumulated', 10000000)
        self.min_inactivity = config.get('alerts', {}).get('min_inactivity_minutes', 2)
        self.top_bazaars = config.get('monitoring', {}).get('top_bazaars_count', 10)
        self.weav3r_batch_size = config.get('monitoring', {}).get('weav3r_batch_size', 10)
        self.weav3r_batch_delay = config.get('monitoring', {}).get('weav3r_batch_delay', 1.0)
        
        self.items_model = MonitoredItemsModel()
        self.detector = get_detector()
        self.tracker = get_tracker()
        
        self.is_running = False
        self.start_time: Optional[datetime] = None
        self.cycle_count = 0
        self.total_sales_detected = 0
        self.total_alerts_sent = 0
        self._player_semaphore = asyncio.Semaphore(100)  # Reused across cycles, no memory leak
        self._last_cleanup = 0  # Unix timestamp of last DB cleanup
        
        # Weav3r discovery cycling state
        self._weav3r_item_offset = 0  # Current position in the monitored items list
        self._weav3r_items_per_minute = 44  # Weav3r rate limit
        self._last_discovery_time = 0  # Unix timestamp of last Weav3r scan
        self._discovery_interval = 60  # Seconds between Weav3r discovery runs
        self._discovered_players_cache: set = set()  # Persists between cycles
    
    async def start(self):
        """Start the monitoring loop."""
        if self.is_running:
            logger.warning("Monitor already running")
            return
        
        self.is_running = True
        self.start_time = datetime.now()
        
        logger.info("=" * 60)
        logger.info("🚀 Torn Mug Bot Monitor Started")
        logger.info(f"Check Interval: {self.check_interval}s")
        logger.info(f"Min Accumulated: ${self.min_accumulated:,}")
        logger.info(f"Min Inactivity: {self.min_inactivity} minutes")
        logger.info(f"Top Bazaars: {self.top_bazaars}")
        logger.info("=" * 60)
        
        # Send startup message to Discord
        try:
            alerter = get_alerter()
            # await alerter.send_info_message("✅ **Mug Bot Started** - Monitoring bazaars for targets...")
        except:
            pass
        
        # Main loop
        while self.is_running:
            try:
                await self._run_cycle()
                await asyncio.sleep(self.check_interval)
            
            except KeyboardInterrupt:
                logger.info("Received shutdown signal")
                break
            
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}", exc_info=True)
                await asyncio.sleep(self.check_interval)
    
    def stop(self):
        """Stop the monitoring loop."""
        logger.info("Stopping monitor...")
        self.is_running = False
    
    async def _run_cycle(self):
        """Run one complete monitoring cycle."""
    
        cycle_start = datetime.now()
        self.cycle_count += 1
    
        logger.info(f"--- Cycle #{self.cycle_count} started ---")
    
        try:
            # ============================================
            # Phase 1: Discovery - Find active players from weav3r
            # Only runs once per minute, cycling 44 items at a time
            # ============================================
            import time as _time
            now_ts = _time.time()
            
            if now_ts - self._last_discovery_time >= self._discovery_interval:
                all_items = await self.items_model.get_enabled_items()
                total_items = len(all_items)
                
                if total_items > 0:
                    # Grab the next 44 items from the rotating offset
                    batch_size = self._weav3r_items_per_minute
                    start = self._weav3r_item_offset
                    
                    if start >= total_items:
                        start = 0
                        self._weav3r_item_offset = 0
                    
                    # Handle wrap-around: if offset+44 exceeds list, wrap to beginning
                    if start + batch_size <= total_items:
                        batch_items = all_items[start:start + batch_size]
                    else:
                        batch_items = all_items[start:] + all_items[:batch_size - (total_items - start)]
                    
                    logger.info(
                        f"🔍 Phase 1: Weav3r discovery — scanning items {start+1}-"
                        f"{min(start + batch_size, total_items)} of {total_items} "
                        f"({len(batch_items)} items this batch)"
                    )
                    
                    new_players = await self.detector.discover_active_players_from_items(
                        items=batch_items,
                        top_n=self.top_bazaars,
                        batch_size=self.weav3r_batch_size,
                        batch_delay=self.weav3r_batch_delay,
                    )
                    
                    # Advance the offset for the next discovery run
                    next_offset = (start + batch_size) % total_items
                    
                    # If we wrapped around, clear the cache to avoid stale player IDs
                    # (they'll be re-discovered on the next full rotation if still active)
                    if next_offset < start:
                        old_size = len(self._discovered_players_cache)
                        # Keep only players that are currently tracked (watch list)
                        watch_players = await self.tracker.targets_model.get_watch_list_players()
                        self._discovered_players_cache = new_players | watch_players
                        logger.info(
                            f"🔄 Full item rotation completed — cache refreshed "
                            f"({old_size} → {len(self._discovered_players_cache)} players)"
                        )
                    else:
                        # Merge into persistent cache
                        self._discovered_players_cache |= new_players
                    
                    self._weav3r_item_offset = next_offset
                    self._last_discovery_time = now_ts
                    
                    logger.info(
                        f"✅ Discovered {len(new_players)} new players this batch, "
                        f"{len(self._discovered_players_cache)} total in cache"
                    )
                else:
                    logger.warning("No items configured for monitoring")
            else:
                seconds_until = int(self._discovery_interval - (now_ts - self._last_discovery_time))
                logger.debug(f"⏳ Weav3r discovery skipped (next scan in {seconds_until}s)")
            
            discovered_players = self._discovered_players_cache
        
            # ============================================
            # Phase 2: Get watch list players
            # ============================================
            watch_list_players = await self.tracker.targets_model.get_watch_list_players()

            # Add VIP players from database (always monitor)
            from database.models import VIPPlayersModel
            vip_model = VIPPlayersModel()
            vip_players = await vip_model.get_all_vips()
            watch_list_players = watch_list_players | vip_players

            logger.info(f"📋 Watch list: {len(watch_list_players)} players being monitored (including {len(vip_players)} VIPs)")
        
            # ============================================
            # Phase 3: Combine into active monitoring set
            # ============================================
            active_monitoring = discovered_players | watch_list_players
        
            logger.info(f"🎯 Total active monitoring: {len(active_monitoring)} players")
        
            if not active_monitoring:
                logger.info("No players to monitor this cycle")
                return
        
            # ============================================
            # Phase 4: Monitor all players (parallel with semaphore)
            # ============================================
            logger.info(f"🚀 Phase 4: Fetching data for {len(active_monitoring)} players in parallel...")
        
            async def monitor_player(player_id):
                async with self._player_semaphore:
                    # Fetch bazaar + profile + job
                    data = await self.tracker.torn_client.fetch_user_data(player_id)
                
                    if not data:
                        return None
                
                    # Detect sales from bazaar comparison
                    sales = await self.detector.detect_sales_for_player(
                        player_id, 
                        data['profile_data']['player_name'],  # ADD player_name from profile
                        data['bazaar']
                    )
                
                    return {
                        'player_id': player_id,
                        'sales': sales,
                        'profile_data': data['profile_data'],
                        'bazaar': data['bazaar'],
                        'bazaar_is_open': data.get('bazaar_is_open', True)
                    }
        
            # Create tasks for all players
            tasks = [monitor_player(pid) for pid in active_monitoring]
        
            # Run all in parallel
            results = await asyncio.gather(*tasks, return_exceptions=True)
        
            # ============================================
            # Phase 5: Process results
            # ============================================
            all_sales = []
            profile_updates = []
            players_to_drop = set()  # Use set to avoid duplicates

            # Get VIP list
            vip_players = set(self.config.get('vip_players', []))

            # Load exception lists once per cycle
            from database.models import ExceptionModel
            exception_model = ExceptionModel()
            excepted_players = await exception_model.get_player_names_set()
            excepted_factions = await exception_model.get_faction_ids_set()

            for result in results:
                if result is None or isinstance(result, Exception):
                    continue
    
                player_id = result['player_id']
                profile_data = result['profile_data']
                
                # Check exception lists — drop instantly if matched
                player_name = profile_data.get('player_name', '')
                faction_id = profile_data.get('faction_id')
                
                if player_name.lower() in excepted_players:
                    logger.info(f"🛡️ Exception: {player_name} ({player_id}) is on player exception list — skipping")
                    target = await self.tracker.targets_model.get_target(player_id)
                    if target:
                        await self.tracker.targets_model.reset_target(player_id)
                    continue
                
                if faction_id and faction_id in excepted_factions:
                    logger.info(f"🛡️ Exception: {player_name} ({player_id}) is in excepted faction {faction_id} — skipping")
                    target = await self.tracker.targets_model.get_target(player_id)
                    if target:
                        await self.tracker.targets_model.reset_target(player_id)
                    continue
        
                # Collect sales (only if player not marked for dropping)
                if result['sales'] and player_id not in players_to_drop:
                    all_sales.extend(result['sales'])
    
                # Collect profile data for batch update (only if not dropped)
                if player_id not in players_to_drop:
                    profile_updates.append({
                        'player_id': player_id,
                        'profile_data': profile_data
                    })

            # Drop players with job protection (non-VIP only)
            if players_to_drop:
                logger.info(f"🛡️ Dropping {len(players_to_drop)} players with Clothing Store protection")
                for player_id in players_to_drop:
                    await self.tracker.targets_model.reset_target(player_id)

            logger.info(f"✅ Monitoring complete: {len(all_sales)} sales detected")
            
            # ============================================
            # Phase 5.5: Drop players who closed their bazaar
            # ============================================
            # Get VIP players from database
            from database.models import VIPPlayersModel
            vip_model = VIPPlayersModel()
            vip_players = await vip_model.get_all_vips()

            for result in results:
                if result is None or isinstance(result, Exception):
                    continue
    
                player_id = result['player_id']
                bazaar_is_open = result.get('bazaar_is_open', True)
    
                # If bazaar closed and not VIP, drop them
                if not bazaar_is_open and player_id not in vip_players:
                    # Check if they're being tracked
                    target = await self.tracker.targets_model.get_target(player_id)
                    if target:
                        logger.info(
                            f"🚫 {target['player_name']} ({player_id}) closed their bazaar - "
                            f"Dropping (was tracking ${target['accumulated_value']:,})"
                        )
                        await self.tracker.targets_model.reset_target(player_id)
    
                # If bazaar closed and VIP, reset accumulated to 0
                elif not bazaar_is_open and player_id in vip_players:
                    target = await self.tracker.targets_model.get_target(player_id)
                    if target:
                        logger.info(
                            f"⭐ VIP {target['player_name']} ({player_id}) closed bazaar - "
                            f"Resetting accumulated from ${target['accumulated_value']:,} to $0"
                        )
                        await self.tracker.targets_model.update_accumulated_and_travel(
                            player_id, 
                            0  # Reset to $0
                        )
        
            # ============================================
            # Phase 6: Process detected sales
            # ============================================
            if all_sales:
                self.total_sales_detected += len(all_sales)
                await self.tracker.process_detected_sales(all_sales)
        
            # ============================================
            # Phase 7: Batch update profile data
            # ============================================
            if profile_updates:
                await self.tracker.targets_model.batch_update_profile_data(profile_updates)
        
            # ============================================
            # Phase 8: Apply business logic (drop rules, travel, etc.)
            # ============================================
            await self.tracker.apply_business_logic()
        
            # ============================================
            # Phase 9: Get targets ready for alerts
            # ============================================
            targets_to_alert = await self.tracker.get_targets_for_alerts(
                self.min_accumulated,
                self.min_inactivity
            )
        
            # ============================================
            # Phase 10: Send alerts
            # ============================================
            if targets_to_alert:
                logger.info(f"Sending alerts for {len(targets_to_alert)} targets")
                alerter = get_alerter()
                await alerter.send_alerts(targets_to_alert)
                self.total_alerts_sent += len(targets_to_alert)
            else:
                logger.debug("No targets ready for alerts")
        
            # ============================================
            # Log cycle stats
            # ============================================
            cycle_duration = (datetime.now() - cycle_start).total_seconds()
            
            logger.info(
                f"--- Cycle #{self.cycle_count} completed in {cycle_duration:.2f}s "
                f"(Sales: {len(all_sales)}, Alerts: {len(targets_to_alert) if targets_to_alert else 0}) ---"
            )
                        
            # Force garbage collection every 10 cycles to free memory
            if self.cycle_count % 10 == 0:
                import gc
                collected = gc.collect()
                logger.debug(f"🗑️ Garbage collection: freed {collected} objects")
            
            # Log memory usage every 50 cycles (~4 minutes at 5s interval)
            if self.cycle_count % 50 == 0:
                try:
                    import psutil, os
                    process = psutil.Process(os.getpid())
                    mem = process.memory_info()
                    mem_mb = mem.rss / (1024 * 1024)
                    logger.info(
                        f"📊 Memory: {mem_mb:.1f} MB RSS | "
                        f"Discovery cache: {len(self._discovered_players_cache)} players"
                    )
                    # Warn if memory is getting high (>800MB on 1GB t2.micro)
                    if mem_mb > 800:
                        logger.warning(f"⚠️ HIGH MEMORY: {mem_mb:.1f} MB — consider restarting")
                except ImportError:
                    pass
            
            # Run database cleanup every 8 hours
            import time
            now = time.time()
            if now - self._last_cleanup > 28800:
                logger.info("🧹 Running scheduled database cleanup...")
                asyncio.create_task(self._run_cleanup())
                self._last_cleanup = now
    
        except Exception as e:
            logger.error(f"Error in cycle: {e}", exc_info=True)
        

    async def _run_cleanup(self):
        """
        Periodic database cleanup — runs every 8 hours.
        Removes old data that piles up and eats RAM/disk.
        Does NOT touch active targets, keys, VIPs, or channels.
        """
        try:
            from database.db import get_database
            db = get_database()
            await db.connect()

            # 1. Delete alert_log older than 3 days
            await db.conn.execute(
                "DELETE FROM alert_log WHERE alerted_at < datetime('now', '-3 days')"
            )

            # 2. Delete transaction_log older than 3 days
            await db.conn.execute(
                "DELETE FROM transaction_log WHERE detected_at < datetime('now', '-3 days')"
            )

            # 3. Delete dropped_targets older than 3 days
            await db.conn.execute(
                "DELETE FROM dropped_targets WHERE dropped_at < datetime('now', '-3 days')"
            )

            # 4. Delete stale bazaar snapshots for players not in tracked_targets
            #    These pile up fastest — every player seen in listings gets a snapshot
            #    but most are never tracked. Clean up snapshots older than 24 hours
            #    for players not currently being tracked.
            await db.conn.execute("""
                DELETE FROM player_bazaar_snapshots 
                WHERE player_id NOT IN (SELECT player_id FROM tracked_targets)
                AND last_updated < datetime('now', '-1 day')
            """)

            await db.conn.commit()

            # 5. VACUUM — reclaim disk space SQLite holds after deletes
            await db.conn.execute("VACUUM")

            logger.info("✅ Database cleanup complete")

        except Exception as e:
            logger.error(f"❌ Cleanup error: {e}")

    def get_stats(self) -> dict:
        """
        Get monitoring statistics.
        
        Returns:
            Dict with stats
        """
        if not self.start_time:
            return {}
        
        uptime = datetime.now() - self.start_time
        uptime_str = str(uptime).split('.')[0]  # Remove microseconds
        
        return {
            'is_running': self.is_running,
            'uptime': uptime_str,
            'uptime_seconds': int(uptime.total_seconds()),
            'cycle_count': self.cycle_count,
            'total_sales_detected': self.total_sales_detected,
            'total_alerts_sent': self.total_alerts_sent,
            'check_interval': self.check_interval,
            'min_accumulated': self.min_accumulated,
            'min_inactivity_minutes': self.min_inactivity
        }


# Global instance
_monitor: Optional[Monitor] = None


def init_monitor(config: dict):
    """
    Initialize global monitor.
    
    Args:
        config: Configuration dict
    """
    global _monitor
    _monitor = Monitor(config)


def get_monitor() -> Monitor:
    """
    Get global monitor instance.
    
    Returns:
        Monitor instance
    """
    if _monitor is None:
        raise RuntimeError("Monitor not initialized. Call init_monitor() first.")
    return _monitor