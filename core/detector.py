"""
Sale detection logic.
Compares individual player bazaar snapshots to detect sales.
"""

from typing import List, Dict, Optional
import asyncio

from database.models import BazaarStateModel, MonitoredItemsModel
from api.weav3r import get_weav3r_client
from utils.logger import get_logger

logger = get_logger(__name__)


class SaleDetector:
    """Detects sales by comparing player bazaar snapshots."""
    
    def __init__(self):
        self.bazaar_model = BazaarStateModel()
        self.weav3r_client = get_weav3r_client()
        self.items_model = MonitoredItemsModel()
    
    async def discover_active_players(
        self,
        top_n: int = 10,
        batch_size: int = 5,
        batch_delay: float = 1.5,
    ) -> set:
        """
        Discover active players from weav3r marketplace (top N listings).
        Fetches items in small batches with a delay between each batch
        so Cloudflare never sees a burst of simultaneous requests.

        Args:
            top_n:       Number of top listings to check per item
            batch_size:  How many items to fetch concurrently per batch
            batch_delay: Seconds to wait between batches

        Returns:
            Set of player_ids currently in top listings
        """
        items = await self.items_model.get_enabled_items()

        if not items:
            logger.warning("No items configured for monitoring")
            return set()

        logger.info(
            f"🔍 Scanning weav3r: {len(items)} items, "
            f"batch_size={batch_size}, delay={batch_delay}s"
        )

        discovered_players = set()

        # Split items into batches and process with delay between each
        for batch_start in range(0, len(items), batch_size):
            batch = items[batch_start: batch_start + batch_size]
            batch_num = (batch_start // batch_size) + 1
            total_batches = (len(items) + batch_size - 1) // batch_size

            logger.debug(f"  Batch {batch_num}/{total_batches}: fetching {len(batch)} items")

            # Fetch this batch concurrently
            tasks = [
                self.weav3r_client.fetch_bazaar_data(item['item_id'], top_n)
                for item in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"❌ Failed to fetch {batch[i]['item_name']}: {result}")
                    continue
                if result:
                    for listing in result:
                        discovered_players.add(listing['player_id'])

            # Wait between batches (skip delay after last batch)
            if batch_start + batch_size < len(items):
                await asyncio.sleep(batch_delay)

        logger.info(f"✅ Discovered {len(discovered_players)} unique players in top listings")
        return discovered_players

    async def detect_sales_for_player(self, player_id: int, player_name: str, current_bazaar: List[dict]) -> List[dict]:
        """
        Detect sales for a single player by comparing bazaar snapshots.

        Args:
            player_id:       Player's Torn ID
            player_name:     Player's name (from profile data)
            current_bazaar:  Current bazaar items from Torn API

        Returns:
            List of detected sales
        """
        sales = []

        previous_snapshot = await self.bazaar_model.get_player_snapshot(player_id)

        if not previous_snapshot:
            await self.bazaar_model.save_player_snapshot(player_id, current_bazaar)
            return sales

        for item in current_bazaar:
            item_id = item['item_id']
            curr_quantity = item['quantity']
            curr_price = item['price']

            if item_id in previous_snapshot:
                prev_quantity = previous_snapshot[item_id]['quantity']
                prev_price = previous_snapshot[item_id]['price']

                if curr_quantity < prev_quantity:
                    quantity_sold = prev_quantity - curr_quantity
                    sale_value = quantity_sold * prev_price

                    sales.append({
                        'player_id': player_id,
                        'player_name': player_name,
                        'item_id': item_id,
                        'item_name': item.get('name', f'Item {item_id}'),
                        'quantity_sold': quantity_sold,
                        'unit_price': prev_price,
                        'total_value': sale_value
                    })

                    logger.info(
                        f"💰 SALE: {player_name} {player_id} sold "
                        f"{quantity_sold}x {item.get('name', item_id)} @ ${prev_price:,} = ${sale_value:,}"
                    )

        for prev_item_id, prev_data in previous_snapshot.items():
            if not any(item['item_id'] == prev_item_id for item in current_bazaar):
                quantity_sold = prev_data['quantity']
                sale_value = quantity_sold * prev_data['price']

                sales.append({
                    'player_id': player_id,
                    'player_name': player_name,
                    'item_id': prev_item_id,
                    'item_name': f'Item {prev_item_id}',
                    'quantity_sold': quantity_sold,
                    'unit_price': prev_data['price'],
                    'total_value': sale_value
                })

                logger.info(
                    f"💰 DELISTED: {player_name} {player_id} sold entire listing "
                    f"{quantity_sold}x item {prev_item_id} @ ${prev_data['price']:,} = ${sale_value:,}"
                )

        await self.bazaar_model.save_player_snapshot(player_id, current_bazaar)
        return sales


# Global instance
_detector: SaleDetector = None


def get_detector() -> SaleDetector:
    global _detector
    if _detector is None:
        _detector = SaleDetector()
    return _detector
