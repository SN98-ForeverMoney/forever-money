from typing import Tuple


class UniswapV3Math:
    """
    Int-only Uniswap V3 math helpers (Q96 fixed point).
    """

    Q96 = 1 << 96
    Q192 = Q96 * Q96
    MIN_TICK = -887272
    MAX_TICK = 887272

    MIN_SQRT_RATIO = 4295128739
    MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342

    @staticmethod
    def sqrt_price_x96_to_price(sqrt_price_x96: int, decimals0: int = 18, decimals1: int = 18) -> float:
        """
        Convert sqrtPriceX96 to human-readable price (token1/token0).

        Args:
            sqrt_price_x96: The sqrtPriceX96 value from slot0
            decimals0: Decimals of token0 (default 18)
            decimals1: Decimals of token1 (default 18)

        Returns:
            Price as float (token1 per token0)
        """
        # price = (sqrtPriceX96 / 2^96)^2
        price = (sqrt_price_x96 / UniswapV3Math.Q96) ** 2
        # Adjust for decimals: price * 10^(decimals0 - decimals1)
        price = price * (10 ** (decimals0 - decimals1))
        return price

    @staticmethod
    def get_sqrt_ratio_at_tick(tick: int) -> int:
        if tick < UniswapV3Math.MIN_TICK or tick > UniswapV3Math.MAX_TICK:
            raise ValueError("T")

        abs_tick = -tick if tick < 0 else tick

        ratio = (
            0xFFFCB933BD6FAD37AA2D162D1A594001
            if abs_tick & 0x1 != 0
            else 0x100000000000000000000000000000000
        )

        if abs_tick & 0x2:
            ratio = (ratio * 0xFFF97272373D413259A46990580E213A) >> 128
        if abs_tick & 0x4:
            ratio = (ratio * 0xFFF2E50F5F656932EF12357CF3C7FDCC) >> 128
        if abs_tick & 0x8:
            ratio = (ratio * 0xFFE5CACA7E10E4E61C3624EAA0941CD0) >> 128
        if abs_tick & 0x10:
            ratio = (ratio * 0xFFCB9843D60F6159C9DB58835C926644) >> 128
        if abs_tick & 0x20:
            ratio = (ratio * 0xFF973B41FA98C081472E6896DFB254C0) >> 128
        if abs_tick & 0x40:
            ratio = (ratio * 0xFF2EA16466C96A3843EC78B326B52861) >> 128
        if abs_tick & 0x80:
            ratio = (ratio * 0xFE5DEE046A99A2A811C461F1969C3053) >> 128
        if abs_tick & 0x100:
            ratio = (ratio * 0xFCBE86C7900A88AEDCFFC83B479AA3A4) >> 128
        if abs_tick & 0x200:
            ratio = (ratio * 0xF987A7253AC413176F2B074CF7815E54) >> 128
        if abs_tick & 0x400:
            ratio = (ratio * 0xF3392B0822B70005940C7A398E4B70F3) >> 128
        if abs_tick & 0x800:
            ratio = (ratio * 0xE7159475A2C29B7443B29C7FA6E889D9) >> 128
        if abs_tick & 0x1000:
            ratio = (ratio * 0xD097F3BDFD2022B8845AD8F792AA5825) >> 128
        if abs_tick & 0x2000:
            ratio = (ratio * 0xA9F746462D870FDF8A65DC1F90E061E5) >> 128
        if abs_tick & 0x4000:
            ratio = (ratio * 0x70D869A156D2A1B890BB3DF62BAF32F7) >> 128
        if abs_tick & 0x8000:
            ratio = (ratio * 0x31BE135F97D08FD981231505542FCFA6) >> 128
        if abs_tick & 0x10000:
            ratio = (ratio * 0x9AA508B5B7A84E1C677DE54F3E99BC9) >> 128
        if abs_tick & 0x20000:
            ratio = (ratio * 0x5D6AF8DEDB81196699C329225EE604) >> 128
        if abs_tick & 0x40000:
            ratio = (ratio * 0x2216E584F5FA1EA926041BEDFE98) >> 128
        if abs_tick & 0x80000:
            ratio = (ratio * 0x48A170391F7DC42444E8FA2) >> 128

        if tick > 0:
            ratio = (1 << 256) // ratio

        # round up to match Solidity
        return (ratio >> 32) + (1 if ratio & ((1 << 32) - 1) != 0 else 0)

    # -----------------------------
    # Liquidity math
    # -----------------------------

    @staticmethod
    def _liquidity_from_amount0(amount0: int, sqrtPA: int, sqrtPB: int) -> int:
        return (amount0 * sqrtPA * sqrtPB) // ((sqrtPB - sqrtPA) * UniswapV3Math.Q96)

    @staticmethod
    def _liquidity_from_amount1(amount1: int, sqrtPA: int, sqrtPB: int) -> int:
        return (amount1 * UniswapV3Math.Q96) // (sqrtPB - sqrtPA)

    @staticmethod
    def get_liquidity_for_amounts(
        sqrtP: int,
        sqrtPA: int,
        sqrtPB: int,
        amount0: int,
        amount1: int,
    ) -> int:
        """
        Liquidity from amounts (Uniswap V3 exact logic).
        """

        if sqrtPA > sqrtPB:
            sqrtPA, sqrtPB = sqrtPB, sqrtPA

        if sqrtP <= sqrtPA:
            return UniswapV3Math._liquidity_from_amount0(amount0, sqrtPA, sqrtPB)

        elif sqrtP < sqrtPB:
            L0 = UniswapV3Math._liquidity_from_amount0(amount0, sqrtP, sqrtPB)
            L1 = UniswapV3Math._liquidity_from_amount1(amount1, sqrtPA, sqrtP)
            return min(L0, L1)

        else:
            return UniswapV3Math._liquidity_from_amount1(amount1, sqrtPA, sqrtPB)

    # -----------------------------
    # Amounts from liquidity
    # -----------------------------

    @staticmethod
    def get_amounts_for_liquidity(
        sqrtP: int,
        sqrtPA: int,
        sqrtPB: int,
        L: int,
    ) -> Tuple[int, int]:
        """
        Amounts from liquidity (Uniswap V3 exact logic).
        Returns (amount0, amount1)
        """

        if sqrtPA > sqrtPB:
            sqrtPA, sqrtPB = sqrtPB, sqrtPA

        if L <= 0:
            return 0, 0

        if sqrtP <= sqrtPA:
            amount0 = (L * (sqrtPB - sqrtPA) * UniswapV3Math.Q96) // (sqrtPA * sqrtPB)
            return amount0, 0

        elif sqrtP < sqrtPB:
            amount0 = (L * (sqrtPB - sqrtP) * UniswapV3Math.Q96) // (sqrtP * sqrtPB)
            amount1 = (L * (sqrtP - sqrtPA)) // UniswapV3Math.Q96
            return amount0, amount1

        else:
            amount1 = (L * (sqrtPB - sqrtPA)) // UniswapV3Math.Q96
            return 0, amount1

    # -----------------------------
    # Position helper
    # -----------------------------

    @staticmethod
    def position_liquidity_and_used_amounts(
        tick_lower: int,
        tick_upper: int,
        sqrt_price_x96: int,
        amount0: int,
        amount1: int,
    ) -> Tuple[int, int, int]:
        """
        Final helper:
        returns (liquidity, used_amount0, used_amount1)
        """

        sqrtPA = UniswapV3Math.get_sqrt_ratio_at_tick(tick_lower)
        sqrtPB = UniswapV3Math.get_sqrt_ratio_at_tick(tick_upper)

        L = UniswapV3Math.get_liquidity_for_amounts(
            sqrt_price_x96,
            sqrtPA,
            sqrtPB,
            amount0,
            amount1,
        )

        used0, used1 = UniswapV3Math.get_amounts_for_liquidity(
            sqrt_price_x96,
            sqrtPA,
            sqrtPB,
            L,
        )

        return L, used0, used1
