"""
Alert generation and sending.
Creates Discord embeds and sends alerts to channel.
"""

import discord
import json
from typing import List, Optional

from database.models import TransactionLogModel

from api.ffscouter import get_ffscouter_client

from database.models import AlertLogModel, MonitoredItemsModel, TrackedTargetsModel
from utils.logger import get_logger
from utils.formatters import format_alert_embed_data

logger = get_logger(__name__)


class Alerter:
    """Handles alert generation and Discord sending."""
    
    def __init__(self, config: dict = None):
        """Initialize alerter."""
        self.config = config or {}
        self.alert_log = AlertLogModel()
        self.items_model = MonitoredItemsModel()
        self.targets_model = TrackedTargetsModel()
        self.bot = None
    
    def set_bot(self, bot):
        """Set Discord bot instance."""
        self.bot = bot
        logger.info("Alerter connected to Discord bot")
        
    async def get_alert_channels(self) -> List[discord.TextChannel]:
        """Get all active alert channels from database."""
        if not self.bot:
            logger.error("Bot not set in alerter")
            return []
        
        from database.db import get_database
        db = get_database()
        await db.connect()
        
        cursor = await db.conn.execute("SELECT channel_id FROM alert_channels")
        rows = await cursor.fetchall()
        
        channels = []
        for row in rows:
            channel = self.bot.get_channel(row['channel_id'])
            if channel:
                channels.append(channel)
            else:
                logger.warning(f"Alert channel {row['channel_id']} not found")
        
        return channels
    
    async def get_filtered_channel_config(self) -> Optional[dict]:
        """Get filtered channel configuration."""
        from database.db import get_database
        db = get_database()
        await db.connect()
    
        cursor = await db.conn.execute("""
            SELECT channel_id, status FROM filtered_channel_config WHERE id = 1
        """)
        row = await cursor.fetchone()
    
        if not row:
            return None
    
        return {
            'channel_id': row['channel_id'],
            'status': row['status']
        }
    
    def set_channel(self, channel: discord.TextChannel):
        """
        Set Discord channel for alerts.
        
        Args:
            channel: Discord channel
        """
        self.channel = channel
        logger.info(f"Alert channel set to: {channel.name} ({channel.id})")
    
    async def send_alerts(self, targets: List[dict]):
        """Send alerts for all targets, routing by stats if configured."""
        if not targets:
            return
    
        main_channels = await self.get_alert_channels()
    
        if not main_channels:
            logger.error("No alert channels configured (use /admin_set_channel)")
            return
    
        item_names = await self.items_model.get_item_names_map()
    
        # Fetch FFScouter stats for all targets at once
        player_ids = [t['player_id'] for t in targets]
        ffscouter = get_ffscouter_client()
        stats_data = await ffscouter.get_stats(player_ids)
    
        # Get filtered channel config (if set)
        filtered_config = await self.get_filtered_channel_config()
    
        logger.info(f"Sending {len(targets)} alerts")
    
        for target in targets:
            # Add FFScouter stats to target
            ffscouter_stats = stats_data.get(target['player_id'])
            target['ffscouter_stats'] = ffscouter_stats
        
            # Determine which channel(s) to send to
            if filtered_config and ffscouter_stats:
                bs_estimate = ffscouter_stats.get('bs_estimate', 0)
                status = filtered_config['status']
            
                if bs_estimate < status:
                    # Low stats - send to filtered channel only
                    filtered_channel = self.bot.get_channel(filtered_config['channel_id'])
                    if filtered_channel:
                        await self._send_single_alert(target, item_names, [filtered_channel])
                    else:
                        logger.warning(f"Filtered channel {filtered_config['channel_id']} not found, sending to main")
                        await self._send_single_alert(target, item_names, main_channels)
                else:
                    # High stats - send to main channels
                    await self._send_single_alert(target, item_names, main_channels)
            else:
                # No filtering or no stats - send to main channels
                await self._send_single_alert(target, item_names, main_channels)
    
    async def _send_single_alert(self, target: dict, item_names: dict, channels: List[discord.TextChannel]):
        """
        Send single alert for a target.
        
        Args:
            target: Target dict
            item_names: Item ID to name mapping
        """
        try:
            
            # GET TRANSACTION HISTORY
            transaction_log = TransactionLogModel()
            transactions = await transaction_log.get_player_transactions(target['player_id'])

            # LOG FULL HISTORY TO CONSOLE
            logger.info("=" * 60)
            logger.info(f"🚨 ALERT: {target['player_name']} ({target['player_id']}) - ${target['accumulated_value']:,}")
            logger.info("📋 TRANSACTION HISTORY:")
        
            for txn in transactions:
                logger.info(
                    f"  [{txn['detected_at']}] {txn['quantity']}x {txn['item_name']} "
                    f"@ ${txn['unit_price']:,} = ${txn['total_value']:,}"
                )
        
            logger.info("=" * 60)
            
            # Apply Clothing Store protection multiplier if applicable
            job_type_id = target.get('job_type_id')
            job_rating = target.get('job_rating')
            has_protection = (job_type_id == 5 and job_rating is not None and job_rating >= 7)

            if has_protection:
                # 75% protection = only 25% muggable
                effective_accumulated = int(target['accumulated_value'] * 0.25)
                protection_note = f"🛡️ Clothing Store {job_rating}⭐ (75% protection - only 25% muggable)"
            else:
                effective_accumulated = target['accumulated_value']
                protection_note = None

            # Parse items_breakdown if it's a JSON string
            if 'sales_breakdown' in target and isinstance(target['sales_breakdown'], str):
                try:
                    target['sales_breakdown'] = json.loads(target['sales_breakdown'])
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse sales_breakdown for player {target['player_id']}")
                    target['sales_breakdown'] = {}
                    
            # Create modified target dict with effective accumulated
            alert_target = target.copy()
            alert_target['accumulated_value'] = effective_accumulated
            
            # Format embed data
            embed_data = format_alert_embed_data(
                target=target,
                config=self.config,
                item_names=item_names
            )
            
            # Create Discord embed
            embed = discord.Embed(
                title=embed_data['title'],
                color=discord.Color.red(),
                timestamp=embed_data['timestamp']
            )
            
            # Add fields
            embed.add_field(
                name="� Max Potential Mug",
                value=f"**{embed_data['max_mug_short']}** ({embed_data['max_mug_full']})",
                inline=True
            )
            
            # Add FFScouter battle stats if available
            ffscouter_stats = target.get('ffscouter_stats')
            if ffscouter_stats:
                bs_human = ffscouter_stats.get('bs_estimate_human', 'Unknown')
                embed.add_field(
                    name="⚔️ Battle Stats",
                    value=f"~{bs_human}",
                    inline=True
                )
                        
            embed.add_field(
                name="⏱️ Last Action",
                value=embed_data['time_info'],
                inline=True
            )
            
            embed.add_field(
                name="📶 Activity Status",
                value=embed_data['status_display_text'],
                inline=True
            )
            
            from datetime import datetime, timezone

            current_tct = datetime.now(timezone.utc)
            tct_display = current_tct.strftime('%Y-%m-%d %H:%M:%S TCT')

            embed.add_field(
                name="🕐 Alert Time",
                value=tct_display,
                inline=True
            )
            
            # Add footer
            # Add footer with TCT time
            from datetime import datetime, timezone
            current_tct = datetime.now(timezone.utc)
            tct_string = current_tct.strftime('%H:%M:%S TCT')

            embed.set_footer(text=f"Player ID: {embed_data['player_id']} • {tct_string}")
            
            # Create view with attack button
            view = discord.ui.View(timeout=None)
            button = discord.ui.Button(
                label="🎯 ATTACK NOW",
                style=discord.ButtonStyle.danger,
                url=embed_data['attack_url']
            )
            view.add_item(button)
            
            # Send to Discord
            # Send to all channels
            for channel in channels:
                try:
                    await channel.send(embed=embed, view=view)
                except Exception as e:
                    logger.error(f"Failed to send alert to channel {channel.id}: {e}")
            
            # Log alert
            await self.alert_log.log_alert(
                player_id=target['player_id'],
                player_name=target['player_name'],
                accumulated_value=target['accumulated_value'],
                last_action_minutes=target['last_action_minutes'],
                status_state=target['status_state']
            )
            
            # Update last_alerted timestamp AND value (CRITICAL CHANGE)
            await self.targets_model.update_last_alerted(
                target['player_id'], 
                target['accumulated_value']  # Pass the value we just alerted
            )
            
            logger.info(
                f"Alert sent: {target['player_name']} ({target['player_id']}) - "
                f"${target['accumulated_value']:,}"
            )
        
        except Exception as e:
            logger.error(f"Failed to send alert for player {target['player_id']}: {e}")
    
    async def send_info_message(self, message: str):
        """
        Send informational message to channel.
        
        Args:
            message: Message text
        """
        if not self.channel:
            logger.warning("No Discord channel set")
            return
        
        try:
            embed = discord.Embed(
                description=message,
                color=discord.Color.blue()
            )
            await self.channel.send(embed=embed)
        
        except Exception as e:
            logger.error(f"Failed to send info message: {e}")
    
    async def send_error_message(self, error: str):
        """
        Send error message to channel.
        
        Args:
            error: Error text
        """
        if not self.channel:
            logger.warning("No Discord channel set")
            return
        
        try:
            embed = discord.Embed(
                title="⚠️ Error",
                description=error,
                color=discord.Color.orange()
            )
            await self.channel.send(embed=embed)
        
        except Exception as e:
            logger.error(f"Failed to send error message: {e}")


# Global instance
_alerter: Optional[Alerter] = None


def init_alerter(config: dict = None):
    """
    Initialize global alerter.
    
    Args:
        config: Config dict
    """
    global _alerter
    _alerter = Alerter(config)


def get_alerter() -> Alerter:
    """
    Get global alerter instance.
    
    Returns:
        Alerter instance
    """
    if _alerter is None:
        raise RuntimeError("Alerter not initialized. Call init_alerter() first.")
    return _alerter