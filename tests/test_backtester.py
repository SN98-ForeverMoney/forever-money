
import pytest
import asyncio
from typing import List, Dict, Any, Optional
from validator.services.backtester import BacktesterService
from validator.repositories.pool import DataSource
from protocol import Inventory, Position
from validator.utils.math import UniswapV3Math

class MockDataSource(DataSource):
    def __init__(self, swap_events: List[Dict], prices: Dict[int, int]):
        self.swap_events = swap_events
        self.prices = prices # block -> sqrt_price

    async def get_swap_events(
        self,
        pair_address: str,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        # Filter by block range
        return [
            e for e in self.swap_events 
            if (start_block is None or e["evt_block_number"] >= start_block) and
               (end_block is None or e["evt_block_number"] <= end_block)
        ]

    async def get_sqrt_price_at_block(
        self, pair_address: str, block_number: int
    ) -> Optional[int]:
        # Return closest price <= block_number
        sorted_blocks = sorted([b for b in self.prices.keys() if b <= block_number], reverse=True)
        if sorted_blocks:
            return self.prices[sorted_blocks[0]]
        return None

    # Implement other abstract methods with dummies
    async def get_mint_events(self, *args, **kwargs): return []
    async def get_burn_events(self, *args, **kwargs): return []
    async def get_collect_events(self, *args, **kwargs): return []
    async def get_fee_growth(self, *args, **kwargs): return {"fee0": 0.0, "fee1": 0.0}
    async def get_tick_at_block(self, *args, **kwargs): return 0


@pytest.mark.asyncio
async def test_backtester_no_price_change():
    # Setup
    initial_price = UniswapV3Math.get_sqrt_ratio_at_tick(0)
    
    # One swap event with same price, small amount
    swap_event = {
        "evt_block_number": 100,
        "sqrt_price_x96": initial_price,
        "amount0": 1000, # User swapped 1000 token0
        "amount1": -1000, # User got 1000 token1 (simplified)
        "liquidity": 1000000, # Pool liquidity
        "id": "event1"
    }
    
    mock_db = MockDataSource(
        swap_events=[swap_event],
        prices={0: initial_price, 100: initial_price, 200: initial_price}
    )
    
    backtester = BacktesterService(data_source=mock_db)
    
    # Strategy: Position covering tick 0
    # Tick 0 is price 1.0. Range [-100, 100]
    pos = Position(
        tick_lower=-100,
        tick_upper=100,
        allocation0="100000",
        allocation1="100000"
    )
    
    rebalance_history = [{
        "block": 0,
        "new_positions": [pos],
        "inventory": Inventory(amount0="0", amount1="0")
    }]
    
    initial_inventory = Inventory(amount0="100000", amount1="100000")
    
    result = await backtester.evaluate_positions_performance(
        pair_address="0x123",
        rebalance_history=rebalance_history,
        start_block=0,
        end_block=200,
        initial_inventory=initial_inventory,
        fee_rate=0.003 # 0.3%
    )
    
    # Assertions
    # Should have collected some fees (on amount0)
    assert result["fees0"] > 0
    assert result["fees1"] == 0
    
    # Impermanent loss should be effectively 0 since price didn't change
    # Small rounding errors are expected in integer math
    assert result["impermanent_loss"] < 1e-4
    
    # Deployed amounts should be > 0
    assert result["amount0_deployed"] > 0
    assert result["amount1_deployed"] > 0

@pytest.mark.asyncio
async def test_backtester_price_goes_up():
    # Pool price is always quoted as token1 / token0
    # Adding token1 pushes the price up → ticks move higher
    # Adding token0 pulls the price down → ticks move lower    
    initial_price = UniswapV3Math.get_sqrt_ratio_at_tick(0)
    final_price = UniswapV3Math.get_sqrt_ratio_at_tick(100) # Price increased
    
    swap_event = {
        "evt_block_number": 100,
        "sqrt_price_x96": final_price,
        "amount0": -1000, 
        "amount1": 2000, # User input 2000 Token1
        "liquidity": 1000000,
        "id": "event1"
    }
    
    mock_db = MockDataSource(
        swap_events=[swap_event],
        prices={0: initial_price, 200: final_price}
    )
    
    backtester = BacktesterService(data_source=mock_db)
    
    pos = Position(
        tick_lower=-100,
        tick_upper=200, # Covers the move
        allocation0="100000",
        allocation1="100000"
    )
    
    rebalance_history = [{
        "block": 0,
        "new_positions": [pos],
        "inventory": Inventory(amount0="0", amount1="0")
    }]
    
    initial_inventory = Inventory(amount0="100000", amount1="100000")
    
    result = await backtester.evaluate_positions_performance(
        pair_address="0x123",
        rebalance_history=rebalance_history,
        start_block=0,
        end_block=200,
        initial_inventory=initial_inventory,
        fee_rate=0.003
    )
    
    # Fees collected on Token1 (input)
    assert result["fees1"] > 0
    assert result["fees0"] == 0
    
    # IL should be > 0 (or close to it) because price changed
    # We held LP while price moved.
    # However, IL calculation depends on HODL vs LP.
    # HODL: 100000 T0 + 100000 T1 at new price.
    # LP: Converted some T0 to T1 as price went up?
    # Wait, as price goes up (T0 more valuable), we SELL T0 for T1.
    # So we end up with LESS T0 and MORE T1 than HODL?
    # Yes. And since T0 is the appreciating asset, we underperform HODL.
    # So IL > 0.
    
    assert result["impermanent_loss"] >= 0

