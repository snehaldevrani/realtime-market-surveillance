"""
Torn API client.
Fetches user profiles with automatic key rotation and rate limit handling.
"""

import aiohttp
from typing import Optional

from api.key_manager import get_key_manager
from utils.logger import get_logger
from utils.parsers import parse_profile_response

logger = get_logger(__name__)


class TornAPIClient:
    """Client for Torn official API."""
    
    def __init__(self, base_url: str = "https://api.torn.com/v2", timeout: int = 10, max_retries: int = 3):
        """
        Initialize Torn API client.
        
        Args:
            base_url: Base URL for Torn API
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts
        """
        self.base_url = base_url
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self.session = None  # ADD THIS
        self._session_created_at = None  # ADD THIS
        self._max_session_age = 3600  # 1 hour - ADD THIS
        
    async def _get_session(self):
        """Get or create persistent session with connection limits."""
        import time
    
        current_time = time.time()
    
        if self.session is None or self.session.closed:
            if self.session and not self.session.closed:
                await self.session.close()
        
            # ADD connection limits to prevent memory bloat
            connector = aiohttp.TCPConnector(
                limit=100,  # Max 100 connections total
                limit_per_host=10,  # Max 10 per host
                ttl_dns_cache=300,  # DNS cache for 5 mins
                force_close=True  # Force close connections
            )
        
            self.session = aiohttp.ClientSession(
                timeout=self.timeout,
                connector=connector
            )
            self._session_created_at = current_time
            logger.debug("Created new aiohttp session with connection limits")
    
        # Refresh old sessions
        elif self._session_created_at and (current_time - self._session_created_at) > self._max_session_age:
            logger.debug("Refreshing old aiohttp session")
            await self.session.close()
        
            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=10,
                ttl_dns_cache=300,
                force_close=True
            )
        
            self.session = aiohttp.ClientSession(
                timeout=self.timeout,
                connector=connector
            )
            self._session_created_at = current_time

        return self.session

    
    async def fetch_user_data(self, user_id: int) -> Optional[dict]:
        """
        Fetch user bazaar, profile, and job data with infinite key rotation.
        Tries all available keys until one works or all are exhausted.

        Args:
            user_id: Torn user ID
    
        Returns:
            Dict with 'bazaar', 'profile_data', 'job_data' keys or None on error
        """
        key_manager = get_key_manager()
    
        # Try getting a key and making request
        while True:
            api_key = key_manager.get_available_key()
        
            if not api_key:
                # All keys exhausted this cycle
                logger.warning(f"⚠️ No API keys available for user {user_id}, skipping this cycle")
                return None
        
            # Make request
            result = await self._make_request(user_id, api_key)
        
            if result is not None:
                return result
        
            # Request failed, loop will try next key automatically
            
    async def fetch_user_icons(self, user_id: int) -> Optional[list]:
        """
        Fetch user icons to check bazaar status.

        Args:
            user_id: Torn user ID
        
        Returns:
            List of icon dicts or None on error
        """
        key_manager = get_key_manager()
    
        # Try getting a key and making request
        while True:
            api_key = key_manager.get_available_key()
        
            if not api_key:
                logger.warning(f"⚠️ No API keys available for icons check on user {user_id}")
                return None
        
            # Make request
            result = await self._make_icons_request(user_id, api_key)
        
            if result is not None:
                return result
    
    async def _make_request(self, user_id: int, api_key: str) -> Optional[dict]:
        """
        Make single API request.
        
        Args:
            user_id: Torn user ID
            api_key: API key to use
            
        Returns:
            Parsed profile dict or None on error
        """
        url = f"{self.base_url}/user/{user_id}"
        params = {
            'selections': 'bazaar,profile,job',
            'striptags': 'true',
            'key': api_key
        }
        
        try:
            session = await self._get_session()  # CHANGE THIS LINE
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    logger.warning(f"Torn API returned status {response.status} for user {user_id}")
                    return None
                    
                data = await response.json()
                    
                # Check for API errors
                if 'error' in data:
                    await self._handle_api_error(data['error'], api_key)
                    return None
                    
                # Parse profile data
                # Parse profile and job data
                # Extract data
                bazaar_data = data.get('bazaar', [])
                bazaar_is_open = data.get('bazaar_is_open', True)  # Default to True if not present
                profile_data = data.get('profile')
                job_data = data.get('job')

                # If bazaar is closed, return empty list (don't treat as sales)
                if not bazaar_is_open:
                    logger.debug(f"🚫 Player {user_id} has closed their bazaar")
                    formatted_bazaar = []
                else:
                    # Format bazaar data
                    formatted_bazaar = []
                    for item in bazaar_data:
                        formatted_bazaar.append({
                            'item_id': item.get('ID'),
                            'name': item.get('name'),
                            'quantity': item.get('quantity'),
                            'price': item.get('price')
                        })

                if not profile_data:
                    logger.error(f"❌ No profile data in response for user {user_id}")
                    return None

                # Parse profile
                parsed_profile = parse_profile_response(profile_data, job_data)

                logger.debug(
                    f"✅ Fetched player {user_id}: {len(formatted_bazaar)} bazaar items, "
                    f"Status: {parsed_profile['status_state']}"
                )

                return {
                    'bazaar': formatted_bazaar,
                    'bazaar_is_open': bazaar_is_open,  # ADD THIS
                    'profile_data': parsed_profile,
                    'job_data': job_data
                }
        
        except aiohttp.ClientError as e:
            logger.error(f"Network error fetching profile for user {user_id}: {e}")
            return None
        
        except Exception as e:
            logger.error(f"Unexpected error fetching profile for user {user_id}: {e}", exc_info=True)
            return None
    
    async def _make_icons_request(self, user_id: int, api_key: str) -> Optional[list]:
        """
        Make API request to fetch user icons.
    
        Args:
            user_id: Torn user ID
            api_key: API key to use
        
        Returns:
            List of icons or None on error
        """
        url = f"{self.base_url}/user/{user_id}/icons"
        params = {
            'key': api_key
        }
    
        try:
            session = await self._get_session()
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    logger.warning(f"❌ Icons API returned status {response.status} for user {user_id}")
                    return None
                
                data = await response.json()
                
                # Check for API errors
                if 'error' in data:
                    await self._handle_api_error(data['error'], api_key)
                    return None
                
                # Return icons list
                icons = data.get('icons', [])
                logger.debug(f"✅ Fetched {len(icons)} icons for user {user_id}")
                return icons
    
        except aiohttp.ClientError as e:
            logger.error(f"❌ Network error fetching icons for user {user_id}: {e}")
            return None
    
        except Exception as e:
            logger.error(f"❌ Unexpected error fetching icons for user {user_id}: {e}")
            return None
    
    async def _handle_api_error(self, error: dict, api_key: str):
        """
        Handle Torn API error response.

        Args:
            error: Error dict from API
            api_key: Key that was used
        """
        error_code = error.get('code')
        error_msg = error.get('error', 'Unknown error')

        key_manager = get_key_manager()

        # Error 5: Too many requests (temporary)
        if error_code == 5:
            key_manager.report_rate_limit(api_key)
    
        # Error 2: Incorrect key (permanent)
        elif error_code == 2:
            # Get bot instance from global
            from bot.discord_bot import get_bot
            bot = get_bot()
            await key_manager.report_invalid_key(api_key, error_code, error_msg, bot)
    
        # Error 13: Inactive account (permanent)
        elif error_code == 13:
            from bot.discord_bot import get_bot
            bot = get_bot()
            await key_manager.report_invalid_key(api_key, error_code, error_msg, bot)
            
        # Error 16: Access level not high enough (permanent)
        elif error_code == 16:
            from bot.discord_bot import get_bot
            bot = get_bot()
            await key_manager.report_invalid_key(api_key, error_code, error_msg, bot)
    
        # Error 18: Paused key (permanent)
        elif error_code == 18:
            from bot.discord_bot import get_bot
            bot = get_bot()
            await key_manager.report_invalid_key(api_key, error_code, error_msg, bot)
    
        # Error 6: Incorrect ID (not a key issue)
        elif error_code == 6:
            logger.warning(f"⚠️ Invalid user ID: {error_msg}")
    
        # Other errors
        else:
            logger.error(f"❌ Torn API error {error_code}: {error_msg}")
    
    async def _sleep(self, seconds: int):
        """
        Async sleep helper.
        
        Args:
            seconds: Seconds to sleep
        """
        import asyncio
        await asyncio.sleep(seconds)
        
    async def close(self):
        """Close the persistent session."""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("Closed Torn API session")


# Global instance
_torn_client: Optional[TornAPIClient] = None


def get_torn_client() -> TornAPIClient:
    """
    Get global Torn API client instance.
    
    Returns:
        TornAPIClient instance
    """
    global _torn_client
    if _torn_client is None:
        _torn_client = TornAPIClient()
    return _torn_client