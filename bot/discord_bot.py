"""
Discord bot setup and initialization.
Handles bot lifecycle, events, and command registration.
"""

import discord
from discord.ext import commands
import asyncio
from typing import Optional

from core.monitor import init_monitor, get_monitor
from core.alerter import init_alerter, get_alerter
from utils.logger import get_logger

import sys
import logging

# Global bot instance for key manager DMs
_bot_instance = None

def get_bot():
    """Get global bot instance."""
    return _bot_instance

logger = get_logger(__name__)


class MugBot(commands.Bot):
    """Custom Discord bot class for Torn Mug Bot."""
    
    def __init__(self, config: dict):
        """
        Initialize the bot.
    
        Args:
            config: Configuration dict
        """
        intents = discord.Intents.default()
        intents.message_content = True
    
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
    
        self.config = config
        self.monitor_task: Optional[asyncio.Task] = None
    
        # Set global bot instance
        global _bot_instance
        _bot_instance = self
    
    async def setup_hook(self):
        """Called when bot is starting up."""
        logger.info("Setting up bot...")
        
        # Load commands cog
        await self.load_extension("bot.commands")
        
        # Sync slash commands
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} command(s)")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")
    
    async def on_ready(self):
        """Called when bot is ready and connected to Discord."""
        logger.info("=" * 60)
        logger.info(f"✅ Bot logged in as {self.user} (ID: {self.user.id})")
        logger.info("=" * 60)
        
        try:
            # Initialize alerter
            init_alerter(self.config)
            alerter = get_alerter()
            alerter.set_bot(self)
            
            # Initialize monitor
            init_monitor(self.config)
            
            # DM admin to add keys
            await self._dm_admin_startup()
            
            # Start monitoring task
            self.monitor_task = asyncio.create_task(self.start_monitoring())
            
        except Exception as e:
            logger.error(f"Error in on_ready: {e}", exc_info=True)
    
    async def _dm_admin_startup(self):
        """DM admin on startup to remind them to add keys."""
        try:
            import os
            admin_id = int(os.getenv("ADMIN_DISCORD_ID", 0))
        
            if admin_id:
                admin_user = await self.fetch_user(admin_id)
            
                message = """🚀 **Mug Bot Started**

    The bot is now online and monitoring.

    ⚠️ **Action Required:** Add your admin API keys using:

    `/admin_add_apikey keys:<comma_separated_keys>`

    The bot will start with registered keys only until you add admin keys.

    Bot will function normally but with reduced API capacity until admin keys are added."""
            
                await admin_user.send(message)
                logger.info("✅ Sent startup DM to admin")
    
        except Exception as e:
            logger.error(f"Failed to DM admin on startup: {e}")
                
    
    async def start_monitoring(self):
        """Start the monitoring loop."""
        try:
            # Wait a bit for everything to initialize
            await asyncio.sleep(2)
                        
            monitor = get_monitor()
            await monitor.start()
        
        except Exception as e:
            logger.error(f"Error in monitoring task: {e}", exc_info=True)
    
    async def on_command_error(self, ctx, error):
        """Handle command errors."""
        if isinstance(error, commands.CommandNotFound):
            return  # Ignore unknown commands
        
        logger.error(f"Command error: {error}", exc_info=error)
    
    async def close(self):
        """Called when bot is shutting down."""
        logger.info("Shutting down bot...")
    
        # Stop monitoring
        try:
            monitor = get_monitor()
            monitor.stop()
        
            if self.monitor_task:
                self.monitor_task.cancel()
                try:
                    await self.monitor_task
                except asyncio.CancelledError:
                    pass
        except:
            pass
    
        # Close all API clients
        try:
            from api.torn import get_torn_client
            from api.weav3r import get_weav3r_client
            from database.db import get_database
        
            torn_client = get_torn_client()
            weav3r_client = get_weav3r_client()
            db = get_database()
        
            logger.info("Closing API connections...")
            await torn_client.close()
            await weav3r_client.close()
            from api.ffscouter import get_ffscouter_client
            ffscouter_client = get_ffscouter_client()
            await ffscouter_client.close()
            await db.disconnect()
        
        except Exception as e:
            logger.error(f"Error closing connections: {e}")
    
        await super().close()
        logger.info("Bot shutdown complete")


async def start_bot(token: str, config: dict):
    """
    Start the Discord bot.
    
    Args:
        token: Discord bot token
        config: Configuration dict
    """
    bot = MugBot(config)
    try:
        await bot.start(token)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Bot error: {e}", exc_info=True)
    finally:
        if not bot.is_closed():
            await bot.close()