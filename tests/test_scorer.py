import pytest
from validator.services.scorer import Scorer
from dataclasses import dataclass

@dataclass
class MockInventory:
    amount0: str
    amount1: str

@pytest.mark.asyncio
async def test_scorer_ideal_case():
    """Perfect performance: 10% return, no loss → no penalty. Score = 0.1."""
    metrics = {
        "initial_value": 1000,
        "final_value": 1100,
        "initial_inventory": MockInventory("1000", "1000"),
        "final_inventory": MockInventory("1000", "1000"),
    }
    score = await Scorer.score_pol_strategy(metrics)
    assert score > 0
    assert isinstance(score, float)
    assert abs(score - 0.1) < 0.01  # 10% return, no loss → 0.1

@pytest.mark.asyncio
async def test_scorer_zero_initial_value():
    metrics = {
        "initial_value": 0,
        "final_value": 100,
        "initial_inventory": MockInventory("0", "0"),
        "final_inventory": MockInventory("0", "0")
    }
    
    score = await Scorer.score_pol_strategy(metrics)
    assert score == float("-inf")

@pytest.mark.asyncio
async def test_scorer_negative_value_gain():
    """Negative return (-10%), no loss → score = -0.1."""
    metrics = {
        "initial_value": 1000,
        "final_value": 900,
        "initial_inventory": MockInventory("1000", "1000"),
        "final_inventory": MockInventory("1000", "1000"),
    }
    score = await Scorer.score_pol_strategy(metrics)
    assert score < 0
    assert abs(score - (-0.1)) < 0.01  # -10% return, no loss → -0.1

@pytest.mark.asyncio
async def test_scorer_inventory_loss_penalty():
    """Test that inventory loss reduces the score."""
    # Case 1: No loss (Reference)
    metrics_ref = {
        "initial_value": 1000,
        "final_value": 1100,
        "initial_inventory": MockInventory("1000", "1000"),
        "final_inventory": MockInventory("1000", "1000")
    }
    score_ref = await Scorer.score_pol_strategy(metrics_ref)
    
    # Case 2: 50% Inventory loss on token0
    metrics_loss = {
        "initial_value": 1000,
        "final_value": 1100, # Same value gain (somehow)
        "initial_inventory": MockInventory("1000", "1000"),
        "final_inventory": MockInventory("500", "1000")
    }
    score_loss = await Scorer.score_pol_strategy(metrics_loss)
    
    assert score_loss < score_ref
    assert score_loss > 0 # Still profitable, but penalized

@pytest.mark.asyncio
async def test_scorer_string_inputs():
    """Test that it handles string inputs for amounts correctly."""
    metrics = {
        "initial_value": 1000,
        "final_value": 1100,
        "initial_inventory": MockInventory("1000", "1000"),
        "final_inventory": MockInventory("1000", "1000")
    }
    score = await Scorer.score_pol_strategy(metrics)
    assert score > 0

@pytest.mark.asyncio
async def test_scorer_zero_initial_inventory_amount():
    """Test handling of 0 initial inventory to avoid division by zero."""
    metrics = {
        "initial_value": 1000,
        "final_value": 1100,
        "initial_inventory": MockInventory("0", "1000"), # Token0 starts at 0
        "final_inventory": MockInventory("0", "1000")
    }
    
    score = await Scorer.score_pol_strategy(metrics)
    assert score > 0
    # Should not raise ZeroDivisionError

@pytest.mark.asyncio
async def test_scorer_massive_loss():
    """Massive value loss + 100% token loss → large negative score."""
    metrics = {
        "initial_value": 1000,
        "final_value": 0,
        "initial_inventory": MockInventory("1000", "1000"),
        "final_inventory": MockInventory("0", "0"),
    }
    score = await Scorer.score_pol_strategy(metrics)
    assert score < -1000


@pytest.mark.asyncio
async def test_scorer_uses_impermanent_loss_when_present():
    """When impermanent_loss is provided, use it for penalty instead of token-delta."""
    # Same value gain, but explicit IL = 0.2
    metrics = {
        "initial_value": 1000,
        "final_value": 1100,
        "initial_inventory": MockInventory("1000", "1000"),
        "final_inventory": MockInventory("1000", "1000"),
        "impermanent_loss": 0.2,
    }
    score = await Scorer.score_pol_strategy(metrics)
    # return 0.1, penalty exp(-10*0.2)~0.135, score ~ 0.0135
    assert 0.01 < score < 0.02
    assert score < 0.1  # vs no penalty


@pytest.mark.asyncio
async def test_scorer_in_range_ratio_bonus():
    """Higher in_range_ratio slightly increases score."""
    base = {
        "initial_value": 1000,
        "final_value": 1100,
        "initial_inventory": MockInventory("1000", "1000"),
        "final_inventory": MockInventory("1000", "1000"),
    }
    low = {**base, "in_range_ratio": 0.0}
    high = {**base, "in_range_ratio": 1.0}
    s_low = await Scorer.score_pol_strategy(low)
    s_high = await Scorer.score_pol_strategy(high)
    assert s_high > s_low
    assert s_low > 0 and s_high > 0


def test_rank_miners_by_score_and_history_no_tie():
    """Best round score wins when no ties."""
    round_scores = {1: 10.0, 2: 20.0, 3: 5.0}
    historic = {1: 0.5, 2: 0.3, 3: 0.8}
    ranked = Scorer.rank_miners_by_score_and_history(round_scores, historic)
    assert ranked[0][0] == 2 and ranked[0][1] == 20.0
    assert [r[0] for r in ranked] == [2, 1, 3]


def test_rank_miners_by_score_and_history_tie_break():
    """Tie-break by historic combined_score when round scores equal."""
    round_scores = {1: 10.0, 2: 10.0, 3: 10.0}
    historic = {1: 0.3, 2: 0.8, 3: 0.5}
    ranked = Scorer.rank_miners_by_score_and_history(round_scores, historic)
    assert [r[0] for r in ranked] == [2, 3, 1]


def test_rank_miners_by_score_and_history_missing_historic():
    """Miners missing from historic get 0.0; ties still broken."""
    round_scores = {1: 10.0, 2: 10.0}
    historic = {1: 0.5}
    ranked = Scorer.rank_miners_by_score_and_history(round_scores, historic)
    assert ranked[0][0] == 1
    assert len(ranked) == 2
