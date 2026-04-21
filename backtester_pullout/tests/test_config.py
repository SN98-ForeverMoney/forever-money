"""Config validation tests."""
import pytest
from pydantic import ValidationError

from backtester_pullout.backtester.config import Config, load_config


VALID_POOL = {
    "address": "0x" + "a" * 40,
    "symbol": "ETH/USDC",
    "token0": "0x" + "1" * 40,
    "token1": "0x" + "2" * 40,
    "decimals0": 18,
    "decimals1": 6,
    "fee_tier": 500,
    "tick_spacing": 10,
    "range": {"type": "tick_width", "width_ticks": 200},
    "position_size_usd": 10000,
}

VALID_BASE = {
    "pools": [VALID_POOL],
    "backtest": {"start_block": 100, "end_block": 200},
    "prediction": {
        "horizon_blocks": 300,
        "noise_sigma": 0.2,
        "vol_bucket_blocks": 30,
    },
    "strategy": {"type": "binary", "params": {"threshold": 0.01}},
}


def test_minimal_valid():
    cfg = Config.model_validate(VALID_BASE)
    assert cfg.pools[0].symbol == "ETH/USDC"
    assert cfg.seed == 42


def test_horizon_bucket_ratio_below_5_rejected():
    bad = {**VALID_BASE, "prediction": {
        "horizon_blocks": 60, "noise_sigma": 0.2, "vol_bucket_blocks": 30,
    }}
    with pytest.raises(ValidationError, match="meaningless"):
        Config.model_validate(bad)


def test_horizon_bucket_ratio_at_5_accepted():
    ok = {**VALID_BASE, "prediction": {
        "horizon_blocks": 150, "noise_sigma": 0.2, "vol_bucket_blocks": 30,
    }}
    Config.model_validate(ok)  # no raise


def test_end_block_must_exceed_start():
    bad = {**VALID_BASE, "backtest": {"start_block": 200, "end_block": 100}}
    with pytest.raises(ValidationError):
        Config.model_validate(bad)


def test_unknown_field_rejected():
    bad = {**VALID_BASE, "extra_field": True}
    with pytest.raises(ValidationError):
        Config.model_validate(bad)


def test_absolute_range_ordering():
    bad = {**VALID_POOL, "range": {
        "type": "absolute", "tick_lower": 100, "tick_upper": 50,
    }}
    with pytest.raises(ValidationError):
        Config.model_validate({**VALID_BASE, "pools": [bad]})


def test_loads_example_yaml():
    cfg = load_config("backtester_pullout/config/example.yaml")
    assert cfg.pools[0].symbol == "ETH/USDC"
    assert cfg.prediction.horizon_blocks == 300
