"""
FFScouter API client for battle stats estimates.
"""

import aiohttp
from typing import Optional, Dict, List
from utils.logger import get_logger

logger = get_logger(__name__)


class FFScouterClient:
    """Client for FFScouter battle stats API."""
    
    def __init__(self, api_key: str = "jPJC7po7vjEJ6wQY"):
        self.api_key = api_key
        self.base_url = "https://ffscouter.com/api/v1/get-stats"
        self.session = None
    
    async def _get_session(self):
        """Get or create persistent session."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        return self.session
    
    async def close(self):
        """Close the session."""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("Closed FFScouter API session")
    
    async def get_stats(self, player_ids: List[int]) -> Dict[int, dict]:
        """
        Get battle stats for multiple players.
        
        Args:
            player_ids: List of player IDs
            
        Returns:
            Dict of {player_id: stats_dict} or empty dict on error
        """
        if not player_ids:
            return {}
        
        # Build URL with comma-separated IDs
        targets_str = ",".join(str(pid) for pid in player_ids)
        url = f"{self.base_url}?key={self.api_key}&targets={targets_str}"
        
        try:
            session = await self._get_session()
            async with session.get(url) as response:
                if response.status != 200:
                    logger.warning(f"FFScouter API returned status {response.status}")
                    return {}
                
                data = await response.json()
                
                # Convert list to dict keyed by player_id
                result = {}
                for item in data:
                    player_id = item.get('player_id')
                    if player_id:
                        result[player_id] = {
                            'bs_estimate': item.get('bs_estimate', 0),
                            'bs_estimate_human': item.get('bs_estimate_human', 'Unknown'),
                            'fair_fight': item.get('fair_fight', 0),
                            'last_updated': item.get('last_updated', 0)
                        }
                
                logger.debug(f"✅ Fetched FFScouter stats for {len(result)} players")
                return result
        
        except Exception as e:
            logger.error(f"❌ FFScouter API error: {e}")
            return {}


# Global instance
_ffscouter_client: Optional[FFScouterClient] = None


def get_ffscouter_client() -> FFScouterClient:
    """Get global FFScouter client instance."""
    global _ffscouter_client
    if _ffscouter_client is None:
        _ffscouter_client = FFScouterClient()
    return _ffscouter_client