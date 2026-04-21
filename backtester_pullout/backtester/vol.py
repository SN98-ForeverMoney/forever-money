"""Realized volatility oracle + seeded noise injection.

Definitions (per spec):
  - Price series: forward-fill the last swap's sqrt_price_x96 → price every
    `vol_bucket_blocks` blocks, across the full backtest range.
  - Log returns: log(p[i] / p[i-1]) per bucket.
  - Realized vol over [t, t+H]: std dev (population, ddof=0) of log returns
    whose bucket END-block lies in (t, t+H].
  - Predicted vol: realized_vol * (1 + eps), eps ~ N(0, noise_sigma). Same
    seed → same eps stream.

Determinism: per-cell RNG is derived from (base_seed, cell_index) via SHA256
so sweep cells don't share noise. For a single run, use `make_rng(seed, 0)`.

Zero-return buckets: buckets with no swaps carry the prior price (return = 0).
That matches user's requirement #4 to "count them" — zero-return buckets ARE
included, deflating vol in quiet periods.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from validator.utils.math import UniswapV3Math


# --- Seed derivation ---------------------------------------------------------

def derive_seed(base_seed: int, cell_index: int) -> int:
    """Deterministic per-cell seed via SHA256.

    Returns a 64-bit integer suitable for numpy.random.default_rng.
    """
    h = hashlib.sha256(f"{base_seed}:{cell_index}".encode()).digest()
    return int.from_bytes(h[:8], "big")


def make_rng(base_seed: int, cell_index: int = 0) -> np.random.Generator:
    return np.random.default_rng(derive_seed(base_seed, cell_index))


# --- Bucketed price series ---------------------------------------------------

def bucketed_price_series(
    swaps: pd.DataFrame,
    bucket_blocks: int,
    *,
    start_block: Optional[int] = None,
    end_block: Optional[int] = None,
) -> pd.DataFrame:
    """Produce a regularly-sampled price series at `bucket_blocks` intervals.

    Each row is the last swap's sqrt_price_x96 at or before the bucket end.
    Buckets with no prior swap get NaN (only possible before the first swap).

    Columns returned: bucket_end_block, sqrt_price_x96 (Python int), price (float).
    The float `price` is token1/token0 in raw uint ratio (no decimal adjustment);
    log returns are ratio-invariant so decimals don't matter for vol.
    """
    if len(swaps) == 0:
        raise ValueError("empty swaps")
    lo = int(start_block if start_block is not None else swaps["block"].iloc[0])
    hi = int(end_block if end_block is not None else swaps["block"].iloc[-1])

    # Bucket end-blocks: lo+B, lo+2B, ... up to <= hi
    bucket_ends = np.arange(lo + bucket_blocks, hi + 1, bucket_blocks, dtype=np.int64)
    if len(bucket_ends) < 2:
        raise ValueError(
            f"range too short: need at least 2 buckets (block span >= "
            f"{2 * bucket_blocks}), got span={hi - lo} with bucket={bucket_blocks}"
        )

    # For each bucket end, pick the last swap with block <= end.
    swap_blocks = swaps["block"].to_numpy()
    sqrt_vals = swaps["sqrt_price_x96"].to_list()  # Python ints — keep precision

    # `searchsorted(side='right') - 1` gives index of last swap with block <= bucket_end
    idx = np.searchsorted(swap_blocks, bucket_ends, side="right") - 1

    sqrt_at_end = [sqrt_vals[i] if i >= 0 else None for i in idx]
    # Convert to float ratio (sqrt_price / 2^96)^2 — price token1 per token0
    # We only need this for log returns, so arbitrary decimal normalization is fine.
    Q96 = UniswapV3Math.Q96
    price = np.array(
        [(float(s) / Q96) ** 2 if s is not None else np.nan for s in sqrt_at_end],
        dtype=np.float64,
    )

    return pd.DataFrame({
        "bucket_end_block": bucket_ends,
        "sqrt_price_x96": sqrt_at_end,  # object dtype, Python ints
        "price": price,
    })


# --- Vol computation ---------------------------------------------------------

@dataclass
class VolOracle:
    """Realized + noisy-predicted vol AND forward log-return at each bucket.

    Vol noise:   predicted = realized_vol * (1 + ε_v),  ε_v ~ N(0, noise_sigma)
    Return noise: predicted = realized_return + ε_r,
                  ε_r ~ N(0, return_noise_k * σ̂_past),  where σ̂_past is the
                  *backward* realized vol over the previous H blocks (no leakage).

    Per-cell RNG seeds for vol and return noise so they don't collide.

    Precomputes per-bucket arrays so queries are O(1) bisect + lookup.
    """
    bucket_ends: np.ndarray
    realized: np.ndarray           # forward realized vol σ_R(t, t+H)
    predicted: np.ndarray          # noisy vol
    bucket_blocks: int
    horizon_blocks: int
    noise_sigma: float
    # Price fields (defaults for back-compat with older callers)
    realized_return: np.ndarray = None
    predicted_return: np.ndarray = None
    return_noise_sigma: float = 0.0   # absolute std-dev in log-return units

    @classmethod
    def build(
        cls,
        swaps: pd.DataFrame,
        bucket_blocks: int,
        horizon_blocks: int,
        noise_sigma: float,
        rng: np.random.Generator,
        *,
        start_block: Optional[int] = None,
        end_block: Optional[int] = None,
        return_noise_sigma: float = 0.0,
    ) -> "VolOracle":
        series = bucketed_price_series(
            swaps, bucket_blocks,
            start_block=start_block, end_block=end_block,
        )
        prices = series["price"].to_numpy(dtype=np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            log_ret = np.log(prices[1:] / prices[:-1])
        log_ret = np.where(np.isfinite(log_ret), log_ret, 0.0)

        n = len(prices)
        ends = series["bucket_end_block"].to_numpy()
        realized = np.full(n, np.nan, dtype=np.float64)
        realized_return = np.full(n, np.nan, dtype=np.float64)

        k = max(1, horizon_blocks // bucket_blocks)

        for i in range(n - 1):
            j_end = min(i + k, len(log_ret))
            window = log_ret[i:j_end]
            if len(window) >= 2:
                realized[i] = float(np.std(window, ddof=0))
                # forward log-return = log(p_{t+H} / p_t) = sum of per-bucket returns
                realized_return[i] = float(np.sum(window))

        # Vol noise: multiplicative (positive quantity)
        eps_v = rng.normal(0.0, noise_sigma, size=n)
        predicted = realized * (1.0 + eps_v)

        # Return noise: simple additive Gaussian with fixed absolute std-dev.
        # σ is in log-return units (e.g., 0.001 = 10 bps of error on a log-return).
        eps_r = rng.normal(0.0, return_noise_sigma, size=n) if return_noise_sigma > 0 \
                else np.zeros(n)
        predicted_return = realized_return + eps_r

        return cls(
            bucket_ends=ends,
            realized=realized,
            predicted=predicted,
            realized_return=realized_return,
            predicted_return=predicted_return,
            bucket_blocks=bucket_blocks,
            horizon_blocks=horizon_blocks,
            noise_sigma=noise_sigma,
            return_noise_sigma=return_noise_sigma,
        )

    def _locate(self, block: int) -> int:
        i = int(np.searchsorted(self.bucket_ends, block, side="right")) - 1
        return i

    def realized_vol(self, block: int) -> float:
        i = self._locate(block)
        return float(self.realized[i]) if i >= 0 else float("nan")

    def predicted_vol(self, block: int) -> float:
        i = self._locate(block)
        return float(self.predicted[i]) if i >= 0 else float("nan")

    def realized_return_at(self, block: int) -> float:
        i = self._locate(block)
        return float(self.realized_return[i]) if i >= 0 else float("nan")

    def predicted_return_at(self, block: int) -> float:
        i = self._locate(block)
        return float(self.predicted_return[i]) if i >= 0 else float("nan")
