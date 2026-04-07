"""
Main entry point for Torn Mug Bot.
Loads configuration, initializes systems, and starts the bot.
"""
import logging
import sys

# Setup logging ONCE, before anything else
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s',
    stream=sys.stdout,
    force=True
)

# Prevent duplicate logs from discord.py
logging.getLogger('discord').setLevel(logging.WARNING)

from aiohttp import web
import asyncio
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from database.db import get_database
from database.models import MonitoredItemsModel
from api.key_manager import init_key_manager, get_key_manager
from bot.discord_bot import start_bot
from utils.logger import setup_logger


# Load environment variables
load_dotenv()


logger = logging.getLogger(__name__)

# Add this new function:
async def start_keepalive_server():
    """
    Start a simple HTTP server for keep-alive pings.
    This prevents Discloud from shutting down due to inactivity.
    """
    async def health_check(request):
        from core.monitor import get_monitor
        try:
            monitor = get_monitor()
            stats = monitor.get_stats()
            
            return web.json_response({
                'status': 'alive',
                'uptime': stats.get('uptime', 'unknown'),
                'cycles': stats.get('cycle_count', 0),
                'is_running': stats.get('is_running', False)
            })
        except:
            return web.json_response({
                'status': 'alive',
                'message': 'Bot is running'
            })
    
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Use PORT from environment (Discloud provides this) or default to 8080
    port = int(os.getenv('PORT', 8080))
    
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"🌐 Keep-alive server started on port {port}")
    
    # Keep server running
    while True:
        await asyncio.sleep(3600)

async def load_config() -> dict:
    """
    Load configuration from config.yaml.
    
    Returns:
        Config dict
    """
    config_path = Path("config.yaml")
    
    if not config_path.exists():
        logger.error("config.yaml not found!")
        sys.exit(1)
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        logger.info("Configuration loaded successfully")
        return config
    
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(1)


async def validate_environment():
    """Validate required environment variables."""
    required_vars = [
        "DISCORD_BOT_TOKEN",
        "ADMIN_DISCORD_ID"
    ]
    
    missing = []
    for var in required_vars:
        if not os.getenv(var):
            missing.append(var)
    
    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        logger.error("Please check your .env file")
        sys.exit(1)
    
    logger.info("Environment variables validated")


async def initialize_database(config: dict):
    """
    Initialize database and create tables.
    
    Args:
        config: Configuration dict
    """
    db_path = config.get('database', {}).get('path', 'data/mug_bot.db')
    db = get_database(db_path)
    
    logger.info("Setting up database...")
    await db.setup_tables()
    
    # Initialize monitored items from config
    items_model = MonitoredItemsModel()
    
    for item_config in config.get('items', []):
        if item_config.get('enabled', True):
            await items_model.add_item(
                item_id=item_config['item_id'],
                item_name=item_config['name'],
            )
            logger.info(f"Added monitored item: {item_config['name']} (ID: {item_config['item_id']})")
    
    logger.info("Database initialized")
    # Migrate VIPs from config to database (one-time migration)
    vip_players_from_config = config.get('vip_players', [])
    if vip_players_from_config:
        from database.models import VIPPlayersModel
        vip_model = VIPPlayersModel()
    
        for vip_id in vip_players_from_config:
            await vip_model.add_vip(vip_id, 0)  # 0 = system migration
    
        logger.info(f"Migrated {len(vip_players_from_config)} VIPs from config to database")


async def initialize_api_keys():
    """Initialize API key manager (empty, keys added via commands)."""
    init_key_manager()
    
    # Load registered keys from database
    key_manager = get_key_manager()
    await key_manager.load_registered_keys()
    
    logger.info("API key manager initialized (use /admin_add_apikey to add admin keys)")
    
# Add to main.py before starting the bot
async def test_weav3r():
    from api.weav3r import get_weav3r_client
    client = get_weav3r_client()
    
    # Test with Xanax (item 206)
    listings = await client.fetch_bazaar_data(206, 10)
    
    if listings:
        logger.info(f"✅ Weav3r API working! Got {len(listings)} listings")
        for listing in listings[:3]:
            logger.info(f"  - {listing['player_name']}: {listing['quantity']}x @ ${listing['price']:,}")
    else:
        logger.error("❌ Weav3r API returned no data!")


async def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("🎯 Torn Mug Bot Starting...")
    logger.info("=" * 60)
    
    # Validate environment
    await validate_environment()
    
    # Load configuration
    config = await load_config()
    
    # Initialize database
    await initialize_database(config)
    
    # Reset database on startup (clear old data)
    logger.info("Resetting database (clearing old tracking data)...")
    db = get_database()
    await db.reset_database()
    logger.info("Database reset complete")
    
    # Initialize API keys
    await initialize_api_keys()
    
    # Get Discord settings
    bot_token = os.getenv("DISCORD_BOT_TOKEN")

    # Test Weav3r (optional, you can keep this)
    await test_weav3r()

    # Start keep-alive server in background
    asyncio.create_task(start_keepalive_server())

    # Start bot
    logger.info("Starting Discord bot...")
    await start_bot(bot_token, config)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Shutdown requested")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        # Proper cleanup
        logger.info("Running cleanup...")
        
        # Give async tasks time to cleanup
        import asyncio
        import time
        time.sleep(1)
        
        # Force exit
        import os
        os._exit(0)