"""
Tests for the Scorer class.
"""
import pytest
from validator.scorer import Scorer
from validator.models import PerformanceMetrics, MinerScore


@pytest.fixture
def scorer():
    """Create a scorer instance."""
    return Scorer(
        performance_weight=0.7,
        lp_alignment_weight=0.3,
        top_n_strategies=3
    )


def test_performance_scoring_single_miner(scorer):
    """Test performance scoring with a single miner."""
    miner_metrics = {
        1: PerformanceMetrics(
            net_pnl=1000.0,
            hodl_pnl=500.0,
            net_pnl_vs_hodl=500.0,
            total_fees_collected=200.0,
            impermanent_loss=0.05,
            num_rebalances=2
        )
    }

    scores = scorer.calculate_performance_scores(miner_metrics)

    assert 1 in scores
    assert scores[1] >= 0.5  # Top performer should get at least 0.5


def test_performance_scoring_top_heavy(scorer):
    """Test that top 3 miners get better scores."""
    miner_metrics = {
        1: PerformanceMetrics(
            net_pnl=1000.0, hodl_pnl=500.0, net_pnl_vs_hodl=500.0,
            total_fees_collected=200.0, impermanent_loss=0.05, num_rebalances=2
        ),
        2: PerformanceMetrics(
            net_pnl=900.0, hodl_pnl=500.0, net_pnl_vs_hodl=400.0,
            total_fees_collected=180.0, impermanent_loss=0.06, num_rebalances=2
        ),
        3: PerformanceMetrics(
            net_pnl=800.0, hodl_pnl=500.0, net_pnl_vs_hodl=300.0,
            total_fees_collected=150.0, impermanent_loss=0.07, num_rebalances=3
        ),
        4: PerformanceMetrics(
            net_pnl=700.0, hodl_pnl=500.0, net_pnl_vs_hodl=200.0,
            total_fees_collected=120.0, impermanent_loss=0.08, num_rebalances=3
        ),
        5: PerformanceMetrics(
            net_pnl=600.0, hodl_pnl=500.0, net_pnl_vs_hodl=100.0,
            total_fees_collected=100.0, impermanent_loss=0.09, num_rebalances=4
        )
    }

    scores = scorer.calculate_performance_scores(miner_metrics)

    # Top 3 should have higher scores than 4 and 5
    assert scores[1] >= 0.5
    assert scores[2] >= 0.5
    assert scores[3] >= 0.5
    assert scores[4] < 0.5
    assert scores[5] < 0.5

    # Scores should be ordered by PnL
    assert scores[1] > scores[2]
    assert scores[2] > scores[3]
    assert scores[3] > scores[4]
    assert scores[4] > scores[5]


def test_lp_alignment_scoring(scorer):
    """Test LP alignment scoring (pro-rata)."""
    vault_fees = {
        1: 1000.0,
        2: 500.0,
        3: 250.0,
        4: 250.0
    }

    lp_scores = scorer.calculate_lp_alignment_scores(vault_fees)

    total_fees = sum(vault_fees.values())

    # Scores should be proportional to fees
    assert lp_scores[1] == 1000.0 / total_fees
    assert lp_scores[2] == 500.0 / total_fees
    assert lp_scores[3] == 250.0 / total_fees
    assert lp_scores[4] == 250.0 / total_fees

    # Sum should equal 1.0
    assert abs(sum(lp_scores.values()) - 1.0) < 0.0001


def test_lp_alignment_scoring_zero_fees(scorer):
    """Test LP alignment scoring when no fees collected."""
    vault_fees = {
        1: 0.0,
        2: 0.0,
        3: 0.0
    }

    lp_scores = scorer.calculate_lp_alignment_scores(vault_fees)

    # All scores should be 0
    assert all(score == 0.0 for score in lp_scores.values())


def test_final_scores_calculation(scorer):
    """Test final weighted score calculation."""
    miner_metrics = {
        1: PerformanceMetrics(
            net_pnl=1000.0, hodl_pnl=500.0, net_pnl_vs_hodl=500.0,
            total_fees_collected=200.0, impermanent_loss=0.05, num_rebalances=2
        ),
        2: PerformanceMetrics(
            net_pnl=900.0, hodl_pnl=500.0, net_pnl_vs_hodl=400.0,
            total_fees_collected=180.0, impermanent_loss=0.06, num_rebalances=2
        )
    }

    vault_fees = {
        1: 1000.0,
        2: 2000.0  # Higher LP fees
    }

    miner_hotkeys = {
        1: "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
        2: "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
    }

    constraint_violations = {
        1: [],
        2: []
    }

    scores = scorer.calculate_final_scores(
        miner_metrics=miner_metrics,
        vault_fees=vault_fees,
        miner_hotkeys=miner_hotkeys,
        constraint_violations=constraint_violations
    )

    assert len(scores) == 2

    # Check structure
    assert all(isinstance(s, MinerScore) for s in scores)

    # Scores should be sorted by final_score
    assert scores[0].final_score >= scores[1].final_score

    # Ranks should be assigned
    assert scores[0].rank == 1
    assert scores[1].rank == 2

    # Final scores should be weighted combination
    for score in scores:
        expected = (
            score.performance_score * 0.7 +
            score.lp_alignment_score * 0.3
        )
        assert abs(score.final_score - expected) < 0.0001


def test_constraint_violations_zero_score(scorer):
    """Test that constraint violations result in zero score."""
    miner_metrics = {
        1: PerformanceMetrics(
            net_pnl=1000.0, hodl_pnl=500.0, net_pnl_vs_hodl=500.0,
            total_fees_collected=200.0, impermanent_loss=0.05, num_rebalances=2
        ),
        2: PerformanceMetrics(
            net_pnl=1200.0, hodl_pnl=500.0, net_pnl_vs_hodl=700.0,
            total_fees_collected=250.0, impermanent_loss=0.15, num_rebalances=2
        )
    }

    vault_fees = {
        1: 1000.0,
        2: 1000.0
    }

    miner_hotkeys = {
        1: "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
        2: "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
    }

    constraint_violations = {
        1: [],
        2: ["Impermanent loss exceeds maximum"]  # Has violation
    }

    scores = scorer.calculate_final_scores(
        miner_metrics=miner_metrics,
        vault_fees=vault_fees,
        miner_hotkeys=miner_hotkeys,
        constraint_violations=constraint_violations
    )

    # Miner 2 should have zero score despite better performance
    miner_2_score = next(s for s in scores if s.miner_uid == 2)
    assert miner_2_score.final_score == 0.0
    assert len(miner_2_score.constraint_violations) > 0


def test_get_winning_strategy(scorer):
    """Test getting the winning strategy."""
    miner_metrics = {
        1: PerformanceMetrics(
            net_pnl=1000.0, hodl_pnl=500.0, net_pnl_vs_hodl=500.0,
            total_fees_collected=200.0, impermanent_loss=0.05, num_rebalances=2
        ),
        2: PerformanceMetrics(
            net_pnl=1500.0, hodl_pnl=500.0, net_pnl_vs_hodl=1000.0,
            total_fees_collected=300.0, impermanent_loss=0.06, num_rebalances=2
        )
    }

    vault_fees = {1: 1000.0, 2: 1000.0}
    miner_hotkeys = {
        1: "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
        2: "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
    }
    constraint_violations = {1: [], 2: []}

    scores = scorer.calculate_final_scores(
        miner_metrics=miner_metrics,
        vault_fees=vault_fees,
        miner_hotkeys=miner_hotkeys,
        constraint_violations=constraint_violations
    )

    winner = scorer.get_winning_strategy(scores)

    # Winner should be miner 2 (better performance)
    assert winner.miner_uid == 2
    assert winner.rank == 1


def test_empty_scores_raises_error(scorer):
    """Test that get_winning_strategy raises error with no scores."""
    with pytest.raises(ValueError, match="No scores provided"):
        scorer.get_winning_strategy([])


def test_negative_pnl_handling(scorer):
    """Test handling of negative PnL."""
    miner_metrics = {
        1: PerformanceMetrics(
            net_pnl=-500.0, hodl_pnl=500.0, net_pnl_vs_hodl=-1000.0,
            total_fees_collected=100.0, impermanent_loss=0.15, num_rebalances=5
        ),
        2: PerformanceMetrics(
            net_pnl=100.0, hodl_pnl=500.0, net_pnl_vs_hodl=-400.0,
            total_fees_collected=50.0, impermanent_loss=0.08, num_rebalances=2
        )
    }

    scores = scorer.calculate_performance_scores(miner_metrics)

    # Both have negative PnL vs HODL, but 2 is better
    assert scores[2] > scores[1]
    assert all(score >= 0 for score in scores.values())
