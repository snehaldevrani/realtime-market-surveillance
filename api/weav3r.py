"""
Weav3r API client using curl-cffi for Cloudflare bypass.
Clean version - no proxy, no playwright.
"""

from curl_cffi.requests import AsyncSession
from typing import Optional, List
import asyncio
import time

from utils.logger import get_logger

logger = get_logger(__name__)


class Weav3rClient:
    """Client for Weav3r bazaar API with Cloudflare bypass."""

    def __init__(self, base_url: str = "https://weav3r.dev/api/marketplace", timeout: int = 15):
        self.base_url = base_url
        self.timeout = timeout
        self.session: Optional[AsyncSession] = None
        self._session_created_at = None
        self._max_session_age = 3600

    async def _get_session(self) -> AsyncSession:
        """Get or create curl-cffi session with auto-refresh."""
        current_time = time.time()

        if self.session is None:
            self.session = AsyncSession(
                impersonate="chrome120",
                timeout=self.timeout
            )
            self._session_created_at = current_time
            logger.debug("Created new curl-cffi session")

        elif self._session_created_at and (current_time - self._session_created_at) > self._max_session_age:
            logger.debug("Refreshing old curl-cffi session")
            try:
                await self.session.close()
            except Exception:
                pass
            self.session = AsyncSession(
                impersonate="chrome120",
                timeout=self.timeout
            )
            self._session_created_at = current_time

        return self.session

    async def close(self):
        """Close the session."""
        if self.session:
            try:
                await self.session.close()
            except Exception:
                pass
            self.session = None
            logger.info("Closed Weav3r API session")

    async def fetch_bazaar_data(self, item_id: int, top_n: int = 10) -> Optional[List[dict]]:
        """
        Fetch bazaar listings for an item.

        Args:
            item_id: Torn item ID
            top_n: Number of top listings to return

        Returns:
            List of listing dicts or None on error
        """
        url = f"{self.base_url}/{item_id}"
        session = await self._get_session()

        try:
            response = await session.get(url)

            if response.status_code == 403:
                logger.error(f"❌ Cloudflare blocked request for item {item_id} (403)")
                return None

            if response.status_code != 200:
                logger.error(
                    f"❌ Weav3r API returned status {response.status_code} for item {item_id}. "
                    f"Response: {response.text[:200]}"
                )
                return None

            data = response.json()
            listings = data.get('listings', [])

            if not listings:
                logger.debug(f"No bazaar listings found for item {item_id}")
                return []

            top_listings = listings[:top_n]

            formatted = []
            for listing in top_listings:
                formatted.append({
                    'item_id': listing.get('item_id'),
                    'player_id': listing.get('player_id'),
                    'player_name': listing.get('player_name'),
                    'quantity': listing.get('quantity'),
                    'price': listing.get('price')
                })

            logger.debug(f"✅ Fetched {len(formatted)} bazaar listings for item {item_id}")
            return formatted

        except Exception as e:
            logger.error(f"❌ Error fetching bazaar data for item {item_id}: {e}", exc_info=True)
            return None


# Global instance
_weav3r_client: Optional[Weav3rClient] = None


def get_weav3r_client() -> Weav3rClient:
    """Get global Weav3r client instance."""
    global _weav3r_client
    if _weav3r_client is None:
        _weav3r_client = Weav3rClient()
    return _weav3r_client