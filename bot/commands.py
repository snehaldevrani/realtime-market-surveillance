"""
Discord bot command handlers.
Implements user commands for status, stats, and recent alerts.
"""

import discord
from discord.ext import commands
from discord import app_commands
import os
import aiohttp
import typing

from core.monitor import get_monitor
from database.models import AlertLogModel, MonitoredItemsModel, ExceptionModel
from database.db import get_database
from api.key_manager import get_key_manager
from utils.logger import get_logger
from utils.formatters import format_stats_message, format_recent_alerts, format_currency

logger = get_logger(__name__)


# Get admin ID from environment
ADMIN_DISCORD_ID = int(os.getenv("ADMIN_DISCORD_ID", 0))

def is_admin():
    """Check if user is admin."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id != ADMIN_DISCORD_ID:
            await interaction.response.send_message("❌ Admin only command", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

class BotCommands(commands.Cog):
    """Command handlers for the bot."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.alert_log = AlertLogModel()
        self.items_model = MonitoredItemsModel()
        self.db = get_database()
        
    @app_commands.command(name="admin_add_apikey", description="[ADMIN] Add API keys to the bot")
    @is_admin()
    async def admin_add_apikey(self, interaction: discord.Interaction, keys: str):
        """
        Add comma-separated API keys (admin only, in-memory).
        
        Args:
            keys: Comma-separated API keys
        """
        await interaction.response.defer(ephemeral=True)
        
        try:
            key_list = [k.strip() for k in keys.split(',') if k.strip()]
            
            if not key_list:
                await interaction.followup.send("❌ No valid keys provided", ephemeral=True)
                return
            
            key_manager = get_key_manager()
            added_count = key_manager.add_admin_keys(key_list)
            
            # Persist to database so they survive restarts
            await key_manager.persist_admin_keys()
            
            await interaction.followup.send(
                f"✅ Added {added_count} admin keys (persisted to database)",
                ephemeral=True
            )
        
        except Exception as e:
            logger.error(f"Error in admin_add_apikey: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
    
    @app_commands.command(name="admin_delete_apikey", description="[ADMIN] Remove a specific API key")
    @is_admin()
    async def admin_delete_apikey(self, interaction: discord.Interaction, key_suffix: str):
        """
        Remove an API key by its last 4 characters.
        
        Args:
            key_suffix: Last 4 characters of the key
        """
        await interaction.response.defer(ephemeral=True)
        
        try:
            key_manager = get_key_manager()
            all_keys = key_manager.get_all_keys()
            
            matching_keys = [k for k in all_keys if k.endswith(key_suffix)]
            
            if not matching_keys:
                await interaction.followup.send(f"❌ No key found ending in '{key_suffix}'", ephemeral=True)
                return
            
            if len(matching_keys) > 1:
                await interaction.followup.send(
                    f"❌ Multiple keys match '{key_suffix}'. Be more specific.",
                    ephemeral=True
                )
                return
            
            key_to_remove = matching_keys[0]
            
            if key_to_remove in key_manager.admin_keys:
                key_manager.admin_keys.remove(key_to_remove)
                del key_manager.key_usage[key_to_remove]
                # Also remove from persistent DB
                db = get_database()
                await db.connect()
                await db.conn.execute("DELETE FROM admin_keys WHERE api_key = ?", (key_to_remove,))
                await db.conn.commit()
                await interaction.followup.send(f"✅ Removed admin key ***{key_suffix}", ephemeral=True)
            else:
                db = get_database()
                await db.connect()
                await db.conn.execute("DELETE FROM registered_keys WHERE api_key = ?", (key_to_remove,))
                await db.conn.commit()
                del key_manager.key_usage[key_to_remove]
                await interaction.followup.send(f"✅ Removed registered key ***{key_suffix}", ephemeral=True)
            
            logger.info(f"🗑️ API key removed by admin")
        
        except Exception as e:
            logger.error(f"Error in admin_delete_apikey: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
    
    @app_commands.command(name="admin_set_channel", description="[ADMIN] Add alert channel")
    @is_admin()
    async def admin_set_channel(self, interaction: discord.Interaction, channel_id: str):
        """
        Add a channel to receive alerts.
        
        Args:
            channel_id: Discord channel ID
        """
        await interaction.response.defer(ephemeral=True)
        
        try:
            channel_id_int = int(channel_id)
            
            channel = self.bot.get_channel(channel_id_int)
            if not channel:
                await interaction.followup.send("❌ Channel not found", ephemeral=True)
                return
            
            db = get_database()
            await db.connect()
            
            try:
                await db.conn.execute("""
                    INSERT INTO alert_channels (channel_id, added_by_discord_id)
                    VALUES (?, ?)
                """, (channel_id_int, interaction.user.id))
                await db.conn.commit()
                
                await interaction.followup.send(f"✅ Added alert channel: {channel.mention}", ephemeral=True)
                logger.info(f"✅ Alert channel {channel.name} ({channel_id_int}) added")
            
            except Exception:
                await interaction.followup.send("❌ Channel already added", ephemeral=True)
        
        except ValueError:
            await interaction.followup.send("❌ Invalid channel ID", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in admin_set_channel: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
    
    @app_commands.command(name="admin_unset_channel", description="[ADMIN] Remove alert channel")
    @is_admin()
    async def admin_unset_channel(self, interaction: discord.Interaction, channel_id: str):
        """
        Remove a channel from alert list.
        
        Args:
            channel_id: Discord channel ID
        """
        await interaction.response.defer(ephemeral=True)
        
        try:
            channel_id_int = int(channel_id)
            
            db = get_database()
            await db.connect()
            
            cursor = await db.conn.execute("DELETE FROM alert_channels WHERE channel_id = ?", (channel_id_int,))
            await db.conn.commit()
            
            if cursor.rowcount > 0:
                await interaction.followup.send(f"✅ Removed alert channel {channel_id}", ephemeral=True)
                logger.info(f"✅ Alert channel {channel_id_int} removed")
            else:
                await interaction.followup.send("❌ Channel not found in alert list", ephemeral=True)
        
        except ValueError:
            await interaction.followup.send("❌ Invalid channel ID", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in admin_unset_channel: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            
    @app_commands.command(name="admin_status", description="[ADMIN] Show full bot statistics including admin keys")
    @is_admin()
    async def admin_status(self, interaction: discord.Interaction):
        """Show full bot status with admin key counts."""
        await interaction.response.defer(ephemeral=True)
    
        try:
            monitor = get_monitor()
            monitor_stats = monitor.get_stats()

            db_stats = await self.db.get_database_stats()
        
            key_manager = get_key_manager()
            key_stats = key_manager.get_stats()

            alerts_24h = await self.alert_log.get_alerts_24h()
        
            embed = discord.Embed(
                title="🔧 Admin Status (Full Stats)",
                color=discord.Color.gold()
            )
        
            embed.add_field(
                name="🔑 API Keys",
                value=(
                    f"**Admin Keys:** {key_stats.get('admin_keys', 0)}\n"
                    f"**Registered Keys:** {key_stats.get('registered_keys', 0)}\n"
                    f"**Active:** {key_stats.get('active', 0)}\n"
                    f"**Rate Limited:** {key_stats.get('rate_limited', 0)}\n"
                    f"**Dropped:** {key_stats.get('permanently_bad', 0)}"
                ),
                inline=True
            )

            embed.add_field(
                name="📊 Stats",
                value=(
                    f"**Uptime:** {monitor_stats.get('uptime', 'N/A')}\n"
                    f"**Cycles:** {monitor_stats.get('cycle_count', 0):,}\n"
                    f"**Requests:** {key_stats.get('total_requests', 0):,}\n"
                    f"**Alerts (24h):** {alerts_24h:,}"
                ),
                inline=True
            )
        
            embed.add_field(
                name="🎯 Tracking",
                value=(
                    f"**Active Targets:** {db_stats.get('tracked_targets', 0)}\n"
                    f"**DB Size:** {db_stats.get('database_size', 'Unknown')}"
                ),
                inline=True
            )
        
            await interaction.followup.send(embed=embed, ephemeral=True)
    
        except Exception as e:
            logger.error(f"Error in admin_status: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            
    @app_commands.command(name="admin_minimum_accumulated", description="[ADMIN] Change minimum accumulated value for alerts")
    @is_admin()
    async def admin_minimum_accumulated(self, interaction: discord.Interaction, amount: int):
        """Change the minimum accumulated value threshold."""
        await interaction.response.defer(ephemeral=True)
        
        # Update the monitor's threshold
        from core.monitor import get_monitor
        monitor = get_monitor()
        
        old_value = monitor.min_accumulated
        monitor.min_accumulated = amount
        
        await interaction.followup.send(
            f"✅ **Minimum Accumulated Changed**\n"
            f"Old: ${old_value:,}\n"
            f"New: ${amount:,}",
            ephemeral=True
        )
        
        logger.info(f"Admin changed min_accumulated from ${old_value:,} to ${amount:,}")

    @app_commands.command(name="admin_count_bazaar", description="[ADMIN] Change number of top bazaar listings to monitor")
    @is_admin()
    async def admin_count_bazaar(self, interaction: discord.Interaction, count: int):
        """Change how many top bazaar listings to monitor per item."""
        await interaction.response.defer(ephemeral=True)
        
        if count < 1 or count > 50:
            await interaction.followup.send("❌ Count must be between 1 and 50", ephemeral=True)
            return
        
        # Update the monitor's top bazaar count
        from core.monitor import get_monitor
        monitor = get_monitor()
        
        old_count = monitor.top_bazaars
        monitor.top_bazaars = count
        
        await interaction.followup.send(
            f"✅ **Top Bazaar Count Changed**\n"
            f"Old: {old_count} listings per item\n"
            f"New: {count} listings per item",
            ephemeral=True
        )
        
        logger.info(f"Admin changed top_bazaars from {old_count} to {count}")

    @app_commands.command(name="admin_cycles_time", description="[ADMIN] Change delay between monitoring cycles")
    @is_admin()
    async def admin_cycles_time(self, interaction: discord.Interaction, seconds: int):
        """Change the delay between monitoring cycles."""
        await interaction.response.defer(ephemeral=True)
        
        if seconds < 1 or seconds > 300:
            await interaction.followup.send("❌ Seconds must be between 1 and 300", ephemeral=True)
            return
        
        # Update the monitor's check interval
        from core.monitor import get_monitor
        monitor = get_monitor()
        
        old_interval = monitor.check_interval
        monitor.check_interval = seconds
        
        await interaction.followup.send(
            f"✅ **Cycle Time Changed**\n"
            f"Old: {old_interval}s between cycles\n"
            f"New: {seconds}s between cycles\n"
            f"⚠️ Takes effect on next cycle",
            ephemeral=True
        )
        
        logger.info(f"Admin changed check_interval from {old_interval}s to {seconds}s")
        
    @app_commands.command(name="admin_set_rate_limit", description="[ADMIN] Change API rate limit per key")
    @is_admin()
    async def admin_set_rate_limit(self, interaction: discord.Interaction, calls_per_minute: int):
        """Change the API rate limit per key per minute."""
        await interaction.response.defer(ephemeral=True)
    
        if calls_per_minute < 10 or calls_per_minute > 60:
            await interaction.followup.send("❌ Limit must be between 10 and 60", ephemeral=True)
            return
    
        key_manager = get_key_manager()
        old_limit = key_manager.rate_limit_per_minute

        if key_manager.set_rate_limit(calls_per_minute):
            await interaction.followup.send(
                f"✅ **Rate Limit Changed**\n"
                f"Old: {old_limit} requests/minute\n"
                f"New: {calls_per_minute} requests/minute\n"
                f"⚠️ Takes effect immediately for all keys",
                ephemeral=True
            )
            logger.info(f"Admin changed rate_limit from {old_limit} to {calls_per_minute}")
        else:
            await interaction.followup.send("❌ Failed to change rate limit", ephemeral=True)
            
    @app_commands.command(name="admin_recent_drops", description="[ADMIN] Show recently dropped targets")
    @is_admin()
    async def admin_recent_drops(self, interaction: discord.Interaction, limit: int = 10):
        """Show recently dropped targets (last 8 hours)."""
        await interaction.response.defer(ephemeral=True)
    
        if limit < 1 or limit > 50:
            await interaction.followup.send("❌ Limit must be between 1 and 50", ephemeral=True)
            return
    
        try:
            from database.models import DroppedTargetsModel
            drop_log = DroppedTargetsModel()
            drops = await drop_log.get_recent_drops(limit=limit, hours=8)
        
            if not drops:
                await interaction.followup.send("✅ No targets dropped in the last 8 hours", ephemeral=True)
                return
        
            embed = discord.Embed(
                title=f"📋 Recently Dropped Targets (Last {len(drops)})",
                description="Targets dropped in the last 8 hours",
                color=discord.Color.orange()
            )

            for i, drop in enumerate(drops, 1):
                player_name = drop.get('player_name', 'Unknown')
                player_id = drop.get('player_id', 0)
                value = drop.get('accumulated_value', 0)
                reason = drop.get('drop_reason', 'Unknown')
                dropped_at = drop.get('dropped_at', '')

                embed.add_field(
                    name=f"{i}. {player_name} ({player_id})",
                    value=(
                        f"💰 Had: {format_currency(value)}\n"
                        f"📍 Reason: {reason}\n"
                        f"🕐 Dropped: {dropped_at}"
                    ),
                    inline=False
                )

            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"Admin viewed recent drops (limit: {limit})")
    
        except Exception as e:
            logger.error(f"Error in admin_recent_drops: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            
    
            
    @app_commands.command(name="admin_vip_list", description="[ADMIN] View all VIP players")
    @is_admin()
    async def admin_vip_list(self, interaction: discord.Interaction):
        """View all VIP players."""
        await interaction.response.defer(ephemeral=True)
    
        try:
            from database.models import VIPPlayersModel, TrackedTargetsModel
            vip_model = VIPPlayersModel()
        
            vip_ids = await vip_model.get_all_vips()
        
            if not vip_ids:
                await interaction.followup.send("ℹ️ No VIP players configured", ephemeral=True)
                return
        
            targets_model = TrackedTargetsModel()

            embed = discord.Embed(
                title="⭐ VIP Players (Always Monitored)",
                description=f"Currently tracking {len(vip_ids)} VIP players",
                color=discord.Color.gold()
            )
        
            for vip_id in vip_ids:
                target = await targets_model.get_target(vip_id)

                if target:
                    player_name = target['player_name']
                    accumulated = target['accumulated_value']
                    status = target.get('status_state', 'Unknown')

                    embed.add_field(
                        name=f"{player_name} ({vip_id})",
                        value=(
                            f"💰 Accumulated: {format_currency(accumulated)}\n"
                            f"📊 Status: {status}"
                        ),
                        inline=True
                    )
                else:
                    embed.add_field(
                        name=f"Player {vip_id}",
                        value="Not currently tracked (no sales yet)",
                        inline=True
                    )

            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info("Admin viewed VIP list")
    
        except Exception as e:
            logger.error(f"Error in admin_vip_list: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="admin_vip_add", description="[ADMIN] Add a VIP player")
    @is_admin()
    async def admin_vip_add(self, interaction: discord.Interaction, player_id: int):
        """Add a player to VIP list."""
        await interaction.response.defer(ephemeral=True)
    
        try:
            from database.models import VIPPlayersModel
            vip_model = VIPPlayersModel()
        
            success = await vip_model.add_vip(player_id, interaction.user.id)
        
            if success:
                await interaction.followup.send(
                    f"✅ Added player {player_id} to VIP list\n"
                    f"This player will always be monitored and never dropped.",
                    ephemeral=True
                )
                logger.info(f"Admin added VIP player {player_id}")
            else:
                await interaction.followup.send(f"⚠️ Player {player_id} is already a VIP", ephemeral=True)
    
        except Exception as e:
            logger.error(f"Error in admin_vip_add: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="admin_vip_remove", description="[ADMIN] Remove a VIP player")
    @is_admin()
    async def admin_vip_remove(self, interaction: discord.Interaction, player_id: int):
        """Remove a player from VIP list."""
        await interaction.response.defer(ephemeral=True)
    
        try:
            from database.models import VIPPlayersModel
            vip_model = VIPPlayersModel()
        
            success = await vip_model.remove_vip(player_id)
        
            if success:
                await interaction.followup.send(
                    f"✅ Removed player {player_id} from VIP list",
                    ephemeral=True
                )
                logger.info(f"Admin removed VIP player {player_id}")
            else:
                await interaction.followup.send(f"⚠️ Player {player_id} was not a VIP", ephemeral=True)
    
        except Exception as e:
            logger.error(f"Error in admin_vip_remove: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="admin_set_ffkey", description="[ADMIN] Set/Change ff key")
    @is_admin()
    async def admin_set_ffkey(self, interaction: discord.Interaction, status: int, channel_id: str):
        """Check status of ff key added/ change it."""
        await interaction.response.defer(ephemeral=True)
    
        try:
            channel_id_int = int(channel_id)
        
            channel = self.bot.get_channel(channel_id_int)
            if not channel:
                await interaction.followup.send("❌ Channel not found", ephemeral=True)
                return
        
            db = get_database()
            await db.connect()
        
            # Delete existing config and insert new one
            await db.conn.execute("DELETE FROM filtered_channel_config WHERE id = 1")
            await db.conn.execute("""
                INSERT INTO filtered_channel_config (id, channel_id, status, added_by_discord_id)
                VALUES (1, ?, ?, ?)
            """, (channel_id_int, status, interaction.user.id))
            await db.conn.commit()
        
            await interaction.followup.send(
                f"✅ **Battle Stats Filter Configured**\n\n"
                f"**Threshold:** {status:,} battle stats\n"
                f"**Filtered Channel:** {channel.mention}\n\n"
                f"Targets with < {status:,} stats → {channel.mention}\n"
                f"Targets with ≥ {status:,} stats → Main channels",
                ephemeral=True
            )
            logger.info(f"Admin set FF filter: {status:,} stats, channel {channel_id_int}")
    
        except ValueError:
            await interaction.followup.send("❌ Invalid channel ID", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in admin_set_ff: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            
    @app_commands.command(name="admin_add_item", description="[ADMIN] Add item to monitoring list")
    @is_admin()
    async def admin_add_item(self, interaction: discord.Interaction, item_id: int, item_name: str):
        """Add an item to the monitoring list."""
        await interaction.response.defer(ephemeral=True)
    
        try:
            from database.models import MonitoredItemsModel
            items_model = MonitoredItemsModel()
        
            await items_model.add_item(item_id, item_name)
        
            await interaction.followup.send(
                f"✅ Added item to monitoring:\n"
                f"**{item_name}** (ID: {item_id})\n\n"
                f"⚠️ Bot will start monitoring this item on the next cycle.",
                ephemeral=True
            )
            logger.info(f"Admin added monitored item: {item_name} ({item_id})")
    
        except Exception as e:
            logger.error(f"Error in admin_add_item: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            
    @app_commands.command(name="admin_remove_item", description="[ADMIN] Remove item from monitoring list")
    @is_admin()
    async def admin_remove_item(self, interaction: discord.Interaction, item_id: int):
        """Remove an item from the monitoring list."""
        await interaction.response.defer(ephemeral=True)
    
        try:
            db = get_database()
            await db.connect()

            # Check if item exists first
            cursor = await db.conn.execute(
                "SELECT item_name FROM monitored_items WHERE item_id = ?",
                (item_id,)
            )
            row = await cursor.fetchone()

            if not row:
                await interaction.followup.send(f"❌ Item {item_id} is not being monitored", ephemeral=True)
                return
        
            item_name = row['item_name']
        
            # Delete the item
            await db.conn.execute("DELETE FROM monitored_items WHERE item_id = ?", (item_id,))
            await db.conn.commit()
        
            await interaction.followup.send(
                f"✅ Removed item from monitoring:\n"
                f"**{item_name}** (ID: {item_id})\n\n"
                f"⚠️ Bot will stop monitoring this item on the next cycle.",
                ephemeral=True
            )
            logger.info(f"Admin removed monitored item: {item_name} ({item_id})")
    
        except Exception as e:
            logger.error(f"Error in admin_remove_item: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="admin_list_items", description="[ADMIN] Show all monitored items")
    @is_admin()
    async def admin_list_items(self, interaction: discord.Interaction):
        """Show all items currently being monitored."""
        await interaction.response.defer(ephemeral=True)
    
        try:
            from database.models import MonitoredItemsModel
            items_model = MonitoredItemsModel()
        
            items = await items_model.get_enabled_items()

            if not items:
                await interaction.followup.send("ℹ️ No items are currently being monitored", ephemeral=True)
                return
        
            embed = discord.Embed(
                title="📦 Monitored Items",
                description=f"Currently monitoring {len(items)} items",
                color=discord.Color.blue()
            )
        
            # Group items in fields of 10 to avoid hitting Discord's field limit
            items_text = []
            for item in items:
                items_text.append(f"**{item['item_name']}** (ID: {item['item_id']})")
        
            # Split into chunks of 10
            chunk_size = 10
            for i in range(0, len(items_text), chunk_size):
                chunk = items_text[i:i+chunk_size]
                embed.add_field(
                    name=f"Items {i+1}-{min(i+chunk_size, len(items))}",
                    value="\n".join(chunk),
                    inline=False
                )
        
            embed.set_footer(text=f"Total: {len(items)} items")
        
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"Admin viewed monitored items list")
    
        except Exception as e:
            logger.error(f"Error in admin_list_items: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="register", description="Register your Torn API key")
    async def register(self, interaction: discord.Interaction, api_key: str):
        """
        Register your Torn API key with the bot.
    
        Args:
            api_key: Your Torn API key
        """
        await interaction.response.defer(ephemeral=True)
    
        try:
            if len(api_key) != 16 or not api_key.isalnum():
                await interaction.followup.send("❌ Invalid API key format", ephemeral=True)
                return
        
            async with aiohttp.ClientSession() as session:
                url = "https://api.torn.com/v2/user/profile"
                params = {'striptags': 'true', 'key': api_key}
            
                async with session.get(url, params=params) as response:
                    if response.status != 200:
                        await interaction.followup.send("❌ API request failed", ephemeral=True)
                        return

                    data = await response.json()

                    if 'error' in data:
                        await interaction.followup.send(f"❌ Invalid API key: {data['error']['error']}", ephemeral=True)
                        return
                
                    profile = data.get('profile', {})
                    torn_id = profile.get('id')
                    torn_name = profile.get('name')
                    profile_image = profile.get('image')
                
                    if not torn_id or not torn_name:
                        await interaction.followup.send("❌ Failed to fetch profile", ephemeral=True)
                        return
        
            db = get_database()
            await db.connect()
        
            try:
                await db.conn.execute("""
                    INSERT INTO registered_keys (api_key, discord_id, torn_user_id, torn_username, profile_image)
                    VALUES (?, ?, ?, ?, ?)
                """, (api_key, interaction.user.id, torn_id, torn_name, profile_image))
                await db.conn.commit()
            
                key_manager = get_key_manager()
                await key_manager.load_registered_keys()
            
                embed = discord.Embed(
                    title="✅ API Key Registered",
                    color=discord.Color.green()
                )
                embed.add_field(name="Torn User", value=f"{torn_name} (#{torn_id})", inline=False)
                embed.add_field(name="Discord User", value=interaction.user.mention, inline=False)
            
                if profile_image:
                    embed.set_thumbnail(url=profile_image)

                await interaction.followup.send(embed=embed, ephemeral=True)
                logger.info(f"✅ User {interaction.user} registered Torn account {torn_name} (#{torn_id})")
        
            except Exception as e:
                if "UNIQUE constraint failed" in str(e):
                    await interaction.followup.send("❌ This API key is already registered", ephemeral=True)
                else:
                    raise
    
        except Exception as e:
            logger.error(f"Error in register: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="unregister", description="Remove your registered API key")
    async def unregister(self, interaction: discord.Interaction):
        """Remove your registered API key from the bot."""
        await interaction.response.defer(ephemeral=True)
    
        try:
            db = get_database()
            await db.connect()
        
            cursor = await db.conn.execute("DELETE FROM registered_keys WHERE discord_id = ?", (interaction.user.id,))
            await db.conn.commit()
        
            if cursor.rowcount > 0:
                key_manager = get_key_manager()
                await key_manager.load_registered_keys()
            
                await interaction.followup.send("✅ Your API key has been removed", ephemeral=False)
                logger.info(f"🗑️ User {interaction.user} unregistered their API key")
            else:
                await interaction.followup.send("❌ No registered key found", ephemeral=True)
    
        except Exception as e:
            logger.error(f"Error in unregister: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
    
    @app_commands.command(name="status", description="Show bot status and statistics")
    async def status(self, interaction: discord.Interaction):
        """Show current bot status (public, hides admin keys)."""
        await interaction.response.defer()
        
        try:
            monitor = get_monitor()
            monitor_stats = monitor.get_stats()
            
            db_stats = await self.db.get_database_stats()
            
            key_manager = get_key_manager()
            key_stats = key_manager.get_stats()
            
            alerts_24h = await self.alert_log.get_alerts_24h()
            
            embed = discord.Embed(
                title="🤖 Mug Bot Status",
                color=discord.Color.green() if monitor_stats.get('is_running') else discord.Color.red(),
                description="Current bot statistics"
            )
            
            status_emoji = "🟢" if monitor_stats.get('is_running') else "🔴"
            embed.add_field(
                name=f"{status_emoji} Status",
                value="Running" if monitor_stats.get('is_running') else "Stopped",
                inline=True
            )
            
            if monitor_stats.get('uptime'):
                embed.add_field(
                    name="⏱️ Uptime",
                    value=monitor_stats['uptime'],
                    inline=True
                )
            
            embed.add_field(
                name="🔄 Cycles",
                value=f"{monitor_stats.get('cycle_count', 0):,}",
                inline=True
            )
            
            embed.add_field(
                name="🎯 Active Targets",
                value=f"{db_stats.get('tracked_targets', 0)}",
                inline=True
            )
            
            embed.add_field(
                name="🚨 Alerts (24h)",
                value=f"{alerts_24h:,}",
                inline=True
            )
            
            # ONLY show registered key count (hide admin keys)
            embed.add_field(
                name="🔑 Registered Keys",
                value=f"{key_stats.get('registered_keys', 0)} active",
                inline=True
            )
            
            embed.add_field(
                name="📊 Total Requests",
                value=f"{key_stats.get('total_requests', 0):,}",
                inline=True
            )
            
            embed.add_field(
                name="💾 Database",
                value=db_stats.get('database_size', 'Unknown'),
                inline=True
            )
            
            embed.set_footer(text="Use /help for commands | Admins: use /admin_status for full stats")
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Status command used by {interaction.user}")
        
        except Exception as e:
            logger.error(f"Error in status command: {e}", exc_info=True)
            await interaction.followup.send("❌ Error retrieving status", ephemeral=True)
    
    @app_commands.command(name="recent", description="Show recent mug alerts")
    @app_commands.describe(limit="Number of alerts to show (default: 10)")
    async def recent(self, interaction: discord.Interaction, limit: int = 10):
        """Show recent alerts."""
        await interaction.response.defer()
        
        try:
            if limit < 1 or limit > 50:
                await interaction.followup.send("❌ Limit must be between 1 and 50", ephemeral=True)
                return
            
            alerts = await self.alert_log.get_recent_alerts(limit)
            
            if not alerts:
                embed = discord.Embed(
                    description="No recent alerts found.",
                    color=discord.Color.blue()
                )
                await interaction.followup.send(embed=embed)
                return

            embed = discord.Embed(
                title=f"📋 Recent Alerts (Last {len(alerts)})",
                color=discord.Color.blue()
            )

            for i, alert in enumerate(alerts, 1):
                player_name = alert.get('player_name', 'Unknown')
                player_id = alert.get('player_id', 0)
                value = alert.get('accumulated_value', 0)
                minutes = alert.get('last_action_minutes', 0)
                status = alert.get('status_state', 'Unknown')
                timestamp = alert.get('alerted_at', '')
                
                embed.add_field(
                    name=f"{i}. {player_name} ({player_id})",
                    value=(
                        f"💰 {format_currency(value)} | "
                        f"⏱️ {minutes}m ago | "
                        f"📍 {status}\n"
                        f"🕐 {timestamp}"
                    ),
                    inline=False
                )

            await interaction.followup.send(embed=embed)
            logger.info(f"Recent command used by {interaction.user} (limit: {limit})")
        
        except Exception as e:
            logger.error(f"Error in recent command: {e}", exc_info=True)
            await interaction.followup.send("❌ Error retrieving recent alerts", ephemeral=True)
    
    @app_commands.command(name="stats", description="Show detailed statistics")
    async def stats(self, interaction: discord.Interaction):
        """Show detailed statistics."""
        await interaction.response.defer()
        
        try:
            monitor = get_monitor()
            monitor_stats = monitor.get_stats()
            
            db_stats = await self.db.get_database_stats()
            
            tracker = monitor.tracker
            total_value = await tracker.get_total_tracked_value()
            
            alerts_24h = await self.alert_log.get_alerts_24h()
            
            items = await self.items_model.get_enabled_items()
            items_str = ", ".join([item['item_name'] for item in items]) if items else "None"
            
            embed = discord.Embed(
                title="📊 Detailed Statistics",
                color=discord.Color.purple()
            )
            
            embed.add_field(
                name="⏱️ Monitoring",
                value=(
                    f"**Uptime:** {monitor_stats.get('uptime', 'N/A')}\n"
                    f"**Cycles:** {monitor_stats.get('cycle_count', 0):,}\n"
                    f"**Interval:** {monitor_stats.get('check_interval', 0)}s"
                ),
                inline=True
            )
            
            embed.add_field(
                name="🔍 Detection",
                value=(
                    f"**Sales Found:** {monitor_stats.get('total_sales_detected', 0):,}\n"
                    f"**Active Targets:** {db_stats.get('tracked_targets', 0)}\n"
                    f"**Total Value:** {format_currency(total_value)}"
                ),
                inline=True
            )
            
            embed.add_field(
                name="🚨 Alerts",
                value=(
                    f"**Total Sent:** {monitor_stats.get('total_alerts_sent', 0):,}\n"
                    f"**Last 24h:** {alerts_24h:,}\n"
                    f"**In Log:** {db_stats.get('total_alerts', 0):,}"
                ),
                inline=True
            )
            
            embed.add_field(
                name="📦 Monitored Items",
                value=items_str,
                inline=False
            )
            
            embed.add_field(
                name="⚙️ Configuration",
                value=(
                    f"**Min Accumulated:** {format_currency(monitor_stats.get('min_accumulated', 0))}\n"
                    f"**Min Inactivity:** {monitor_stats.get('min_inactivity_minutes', 0)} minutes"
                ),
                inline=False
            )
            
            embed.add_field(
                name="💾 Database",
                value=(
                    f"**Size:** {db_stats.get('database_size', 'Unknown')}\n"
                    f"**Bazaar Records:** {db_stats.get('bazaar_records', 0):,}\n"
                    f"**Alert Records:** {db_stats.get('total_alerts', 0):,}"
                ),
                inline=False
            )
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Stats command used by {interaction.user}")
        
        except Exception as e:
            logger.error(f"Error in stats command: {e}", exc_info=True)
            await interaction.followup.send("❌ Error retrieving statistics", ephemeral=True)
    
    @app_commands.command(name="admin_manage_exception", description="[ADMIN] Manage player/faction exception list")
    @app_commands.describe(
        exception_type="Player or Faction",
        action="Add, Remove, or List exceptions",
        value="Player username or Faction ID (not needed for List)"
    )
    @app_commands.choices(
        exception_type=[
            app_commands.Choice(name="Player", value="player"),
            app_commands.Choice(name="Faction", value="faction"),
        ],
        action=[
            app_commands.Choice(name="Add", value="add"),
            app_commands.Choice(name="Remove", value="remove"),
            app_commands.Choice(name="List", value="list"),
        ]
    )
    @is_admin()
    async def admin_manage_exception(
        self,
        interaction: discord.Interaction,
        exception_type: app_commands.Choice[str],
        action: app_commands.Choice[str],
        value: str = None
    ):
        """Manage exception lists — excepted players/factions are instantly dropped and never mugged."""
        await interaction.response.defer(ephemeral=True)
        
        try:
            exception_model = ExceptionModel()
            
            if exception_type.value == "player":
                if action.value == "add":
                    if not value:
                        await interaction.followup.send("❌ Please provide a player username", ephemeral=True)
                        return
                    added = await exception_model.add_player(value.strip(), interaction.user.id)
                    if added:
                        await interaction.followup.send(f"✅ Added **{value.strip()}** to player exception list", ephemeral=True)
                    else:
                        await interaction.followup.send(f"❌ **{value.strip()}** is already in the exception list", ephemeral=True)
                
                elif action.value == "remove":
                    if not value:
                        await interaction.followup.send("❌ Please provide a player username", ephemeral=True)
                        return
                    removed = await exception_model.remove_player(value.strip())
                    if removed:
                        await interaction.followup.send(f"✅ Removed **{value.strip()}** from player exception list", ephemeral=True)
                    else:
                        await interaction.followup.send(f"❌ **{value.strip()}** not found in exception list", ephemeral=True)
                
                elif action.value == "list":
                    players = await exception_model.get_all_players()
                    if players:
                        player_list = "\n".join(f"• {name}" for name in players)
                        embed = discord.Embed(
                            title="🛡️ Excepted Players",
                            description=player_list,
                            color=discord.Color.green()
                        )
                        embed.set_footer(text=f"{len(players)} player(s) excepted")
                        await interaction.followup.send(embed=embed, ephemeral=True)
                    else:
                        await interaction.followup.send("No players in exception list", ephemeral=True)
            
            elif exception_type.value == "faction":
                if action.value == "add":
                    if not value:
                        await interaction.followup.send("❌ Please provide a faction ID", ephemeral=True)
                        return
                    try:
                        faction_id = int(value.strip())
                    except ValueError:
                        await interaction.followup.send("❌ Faction ID must be a number", ephemeral=True)
                        return
                    faction_name = f"Faction #{faction_id}"
                    added = await exception_model.add_faction(faction_id, faction_name, interaction.user.id)
                    if added:
                        await interaction.followup.send(f"✅ Added faction **{faction_id}** to exception list", ephemeral=True)
                    else:
                        await interaction.followup.send(f"❌ Faction **{faction_id}** is already in the exception list", ephemeral=True)
                
                elif action.value == "remove":
                    if not value:
                        await interaction.followup.send("❌ Please provide a faction ID", ephemeral=True)
                        return
                    try:
                        faction_id = int(value.strip())
                    except ValueError:
                        await interaction.followup.send("❌ Faction ID must be a number", ephemeral=True)
                        return
                    removed = await exception_model.remove_faction(faction_id)
                    if removed:
                        await interaction.followup.send(f"✅ Removed faction **{faction_id}** from exception list", ephemeral=True)
                    else:
                        await interaction.followup.send(f"❌ Faction **{faction_id}** not found in exception list", ephemeral=True)
                
                elif action.value == "list":
                    factions = await exception_model.get_all_factions()
                    if factions:
                        faction_list = "\n".join(f"• {f['faction_name']} (ID: {f['faction_id']})" for f in factions)
                        embed = discord.Embed(
                            title="🛡️ Excepted Factions",
                            description=faction_list,
                            color=discord.Color.green()
                        )
                        embed.set_footer(text=f"{len(factions)} faction(s) excepted")
                        await interaction.followup.send(embed=embed, ephemeral=True)
                    else:
                        await interaction.followup.send("No factions in exception list", ephemeral=True)
        
        except Exception as e:
            logger.error(f"Error in admin_manage_exception: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="intro", description="Introduction and tutorial for new users")
    async def intro(self, interaction: discord.Interaction):
        """Show intro and tutorial."""
        embed = discord.Embed(
            title="👋 Hey folks, ShadowCrest here!",
            description="Here's a quick tutorial to use this product.",
            color=discord.Color.gold()
        )
        
        embed.add_field(
            name="🔑 Getting Started",
            value=(
                "Start by registering using the `/register` command and put your **PUBLIC** API key. "
                "You will get a message when you do, and also when your key expires from the system due to any reason."
            ),
            inline=False
        )
        
        embed.add_field(
            name="🎯 How Alerts Work",
            value=(
                "When a mug target appears, this channel will be notified. "
                "Click on **Attack Now** and proceed with the mug."
            ),
            inline=False
        )
        
        embed.add_field(
            name="⚠️ NOTE: False Alerts",
            value=(
                "Sometimes (rare) due to issues with the server or with Torn API, there can be false alerts. "
                "Some common false alerts are:\n\n"
                "**1.** Last Action being recent (~2 minutes) and API not updated, "
                "so it's possible they are not a target anymore.\n"
                "**2.** Player turned off their bazaar and API/server didn't register it, "
                "showing absurd amounts being considered as sales."
            ),
            inline=False
        )
        
        embed.add_field(
            name="\u200b",
            value="**Mug Wisely.** 🧠",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed)
        logger.info(f"Intro command used by {interaction.user}")

    @app_commands.command(name="help", description="Show help information")
    async def help_command(self, interaction: discord.Interaction):
        """Show help information for users."""
        embed = discord.Embed(
            title="📖 Mug Bot Help",
            description="Commands available to all users",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="👋 /intro",
            value="New here? Start with this — tutorial on how everything works and how to get set up.",
            inline=False
        )
        
        embed.add_field(
            name="🔑 /register api_key:<key>",
            value="Register your PUBLIC Torn API key to contribute to the bot's key pool. You'll be DM'd if your key gets dropped.",
            inline=False
        )
        
        embed.add_field(
            name="🔓 /unregister",
            value="Remove your API key from the bot.",
            inline=False
        )
        
        embed.add_field(
            name="📡 /status",
            value="Quick overview — bot status, uptime, active targets, alerts in last 24h, and active API keys.",
            inline=False
        )
        
        embed.add_field(
            name="📋 /recent [limit]",
            value="Shows recent mug alerts (default 10, max 50).",
            inline=False
        )
        
        embed.add_field(
            name="📊 /stats",
            value="Detailed stats — uptime, sales detected, monitored items, alert history, and thresholds.",
            inline=False
        )
        
        embed.add_field(
            name="❓ /help",
            value="Shows this help message.",
            inline=False
        )
        
        embed.set_footer(text="Developed for Torn City")
        
        await interaction.response.send_message(embed=embed)
        logger.info(f"Help command used by {interaction.user}")

    @app_commands.command(name="admin_help", description="[ADMIN] Show all admin commands")
    @is_admin()
    async def admin_help(self, interaction: discord.Interaction):
        """Show all admin commands."""
        await interaction.response.defer(ephemeral=True)
        
        embed = discord.Embed(
            title="🔧 Admin Commands",
            description="All commands restricted to admin only",
            color=discord.Color.orange()
        )
        
        embed.add_field(
            name="🔑 API Key Management",
            value=(
                "**/admin_add_apikey** keys:<csv> — Add API keys (persisted to DB)\n"
                "**/admin_delete_apikey** key_suffix:<last 4> — Remove a key by suffix\n"
                "**/admin_set_rate_limit** calls_per_minute:<10-60> — Change rate limit per key"
            ),
            inline=False
        )
        
        embed.add_field(
            name="📢 Channel Management",
            value=(
                "**/admin_set_channel** channel_id:<id> — Add an alert channel\n"
                "**/admin_unset_channel** channel_id:<id> — Remove an alert channel"
            ),
            inline=False
        )
        
        embed.add_field(
            name="⚙️ Monitoring Tuning",
            value=(
                "**/admin_minimum_accumulated** amount:<$> — Change min accumulated for alerts\n"
                "**/admin_count_bazaar** count:<1-50> — Change top bazaar listings to monitor\n"
                "**/admin_cycles_time** seconds:<1-300> — Change delay between cycles"
            ),
            inline=False
        )
        
        embed.add_field(
            name="📦 Item Management",
            value=(
                "**/admin_add_item** item_id:<id> item_name:<name> — Add item to monitoring\n"
                "**/admin_remove_item** item_id:<id> — Remove item from monitoring\n"
                "**/admin_list_items** — Show all monitored items"
            ),
            inline=False
        )
        
        embed.add_field(
            name="⭐ VIP Management",
            value=(
                "**/admin_vip_add** player_id:<id> — Add VIP (never dropped)\n"
                "**/admin_vip_remove** player_id:<id> — Remove VIP\n"
                "**/admin_vip_list** — Show all VIP players"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🛡️ Exception Management",
            value=(
                "**/admin_manage_exception** — Add/Remove/List excepted players or factions\n"
                "  • Excepted players/factions are instantly dropped and never mugged"
            ),
            inline=False
        )
        
        embed.add_field(
            name="📊 Admin Info",
            value=(
                "**/admin_status** — Full stats including admin key count\n"
                "**/admin_recent_drops** [limit] — Show recently dropped targets\n"
                "**/admin_set_ffkey** status:<threshold> channel_id:<id> — Configure battle stats filtering\n"
                "**/admin_help** — This help message"
            ),
            inline=False
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.info(f"Admin help command used by {interaction.user}")


async def setup(bot: commands.Bot):
    """Setup function for loading the cog."""
    await bot.add_cog(BotCommands(bot))