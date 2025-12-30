import asyncio
from typing import Dict, Any

from validator.utils.web3 import AsyncWeb3Helper
from validator.utils.math import UniswapV3Math
from validator.models.job import Job


class Scorer:

    @staticmethod
    async def score_pol_strategy(
        job: Job,
        performance_metrics: Dict[str, Any],
        ratio_penalty_power: float = 2.0,
        fee_weight: float = 0.1,
    ) -> float:
        """
        - Ratio: based on normalized token counts
        - Value & fees: based on Uniswap price (ints)
        """
        pair = AsyncWeb3Helper.make_web3(job.chain_id).make_contract_by_name(name="ICLPool", addr=job.pair_address)
        token0_addr, token1_addr = await asyncio.gather(
            pair.functions.token0().call(),
            pair.functions.token1().call(),
        )
        token0, token1 = (
            AsyncWeb3Helper.make_web3(job.chain_id).make_contract_by_name(name="ERC20", addr=token0_addr),
            AsyncWeb3Helper.make_web3(job.chain_id).make_contract_by_name(name="ERC20", addr=token1_addr),
        )
        token0_decimals, token1_decimals = await asyncio.gather(
            token0.functions.decimals(), token1.functions.decimals()
        )
        amount0_raw, amount1_raw = performance_metrics["amount0_holdings"], performance_metrics["amount1_holdings"]
        fees0, fees1 = performance_metrics["fees0"], performance_metrics["fees1"]
        final_sqrt_price_x96 = performance_metrics["final_sqrt_price_x96"]

        # TOKEN COUNT RATIO (decimals matter)
        amount0_tokens = amount0_raw / (10 ** token0_decimals)
        amount1_tokens = amount1_raw / (10 ** token1_decimals)

        total_tokens = amount0_tokens + amount1_tokens
        if total_tokens <= 0:
            return float("-inf")

        actual_ratio = amount0_tokens / total_tokens
        ratio_error = abs(actual_ratio - job.target_ratio) / job.target_ratio
        ratio_penalty = 1.0 / (1.0 + ratio_error ** ratio_penalty_power)

        # ECONOMIC VALUE (decimals DO NOT matter)
        price_x192 = final_sqrt_price_x96 * final_sqrt_price_x96

        value0_token1 = (amount0_raw * price_x192) // UniswapV3Math.Q192
        total_value_token1 = value0_token1 + amount1_raw

        if total_value_token1 <= 0:
            return float("-inf")

        # FEES (token1 units)
        fees_value_token1 = (
                (fees0 * price_x192) // UniswapV3Math.Q192
                + fees1
        )

        # FINAL SCORE
        score = (
                float(total_value_token1) * ratio_penalty
                + fee_weight * float(fees_value_token1)
        )

        return score


