
import pytest
from validator.utils.math import UniswapV3Math

class TestUniswapV3Math:
    def test_get_sqrt_ratio_at_tick_zero(self):
        # Tick 0 should be exactly 2^96
        expected = 1 << 96
        assert UniswapV3Math.get_sqrt_ratio_at_tick(0) == expected

    def test_get_sqrt_ratio_at_tick_min(self):
        # Min tick
        tick = UniswapV3Math.MIN_TICK
        ratio = UniswapV3Math.get_sqrt_ratio_at_tick(tick)
        assert ratio == UniswapV3Math.MIN_SQRT_RATIO

    def test_get_sqrt_ratio_at_tick_max(self):
        # Max tick
        tick = UniswapV3Math.MAX_TICK
        ratio = UniswapV3Math.get_sqrt_ratio_at_tick(tick)
        assert ratio == UniswapV3Math.MAX_SQRT_RATIO

    def test_liquidity_calculation_in_range(self):
        # Price is within range [tick_lower, tick_upper]
        tick_lower = -100
        tick_upper = 100
        # Price at tick 0
        sqrt_price_x96 = UniswapV3Math.get_sqrt_ratio_at_tick(0)
        
        amount0 = 10**18
        amount1 = 10**18
        
        sqrtPA = UniswapV3Math.get_sqrt_ratio_at_tick(tick_lower)
        sqrtPB = UniswapV3Math.get_sqrt_ratio_at_tick(tick_upper)
        
        liquidity = UniswapV3Math.get_liquidity_for_amounts(
            sqrt_price_x96, sqrtPA, sqrtPB, amount0, amount1
        )
        
        # Calculate expected amounts back
        res_amount0, res_amount1 = UniswapV3Math.get_amounts_for_liquidity(
            sqrt_price_x96, sqrtPA, sqrtPB, liquidity
        )
        
        # Should be close to original amounts (allowing for some rounding error)
        # Note: In V3, you might not use all of both tokens, but the function should return used amounts.
        # Actually get_amounts_for_liquidity returns amounts needed for L.
        
        # Let's verify we didn't use MORE than available
        assert res_amount0 <= amount0
        assert res_amount1 <= amount1
        
        # And at least one should be very close to the limit (limiting factor)
        assert res_amount0 >= amount0 * 0.99 or res_amount1 >= amount1 * 0.99

    def test_liquidity_calculation_below_range(self):
        # Price is below range (tick < tick_lower)
        # Only token0 is needed (asset is cheap, so we hold the other one? No, wait.)
        # If Price < Lower < Upper: We hold Token0 only (buy low)
        # Wait: If P < Pa, current price is low. 
        # Range is above current price. To enter that range (price goes up), we need to sell Token0 for Token1?
        # Actually:
        # If P < Pa: Price is below range. The position is 100% Token0.
        # Because as price moves up into range, we sell Token0 for Token1.
        
        tick_lower = 1000
        tick_upper = 2000
        sqrt_price_x96 = UniswapV3Math.get_sqrt_ratio_at_tick(0) # 0 < 1000
        
        sqrtPA = UniswapV3Math.get_sqrt_ratio_at_tick(tick_lower)
        sqrtPB = UniswapV3Math.get_sqrt_ratio_at_tick(tick_upper)
        
        amount0 = 10**18
        amount1 = 10**18
        
        liquidity = UniswapV3Math.get_liquidity_for_amounts(
            sqrt_price_x96, sqrtPA, sqrtPB, amount0, amount1
        )
        
        # Should only use amount0
        res0, res1 = UniswapV3Math.get_amounts_for_liquidity(
            sqrt_price_x96, sqrtPA, sqrtPB, liquidity
        )
        
        assert res1 == 0
        assert res0 > 0
        assert res0 <= amount0

    def test_liquidity_calculation_above_range(self):
        # Price is above range (tick > tick_upper)
        # The position is 100% Token1.
        
        tick_lower = -2000
        tick_upper = -1000
        sqrt_price_x96 = UniswapV3Math.get_sqrt_ratio_at_tick(0) # 0 > -1000
        
        sqrtPA = UniswapV3Math.get_sqrt_ratio_at_tick(tick_lower)
        sqrtPB = UniswapV3Math.get_sqrt_ratio_at_tick(tick_upper)
        
        amount0 = 10**18
        amount1 = 10**18
        
        liquidity = UniswapV3Math.get_liquidity_for_amounts(
            sqrt_price_x96, sqrtPA, sqrtPB, amount0, amount1
        )
        
        # Should only use amount1
        res0, res1 = UniswapV3Math.get_amounts_for_liquidity(
            sqrt_price_x96, sqrtPA, sqrtPB, liquidity
        )
        
        assert res0 == 0
        assert res1 > 0
        assert res1 <= amount1

