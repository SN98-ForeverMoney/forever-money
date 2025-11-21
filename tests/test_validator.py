"""
Tests for the SN98Validator class.
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from validator.validator import SN98Validator
from validator.models import (
    ValidatorRequest,
    MinerResponse,
    Strategy,
    Position,
    RebalanceRule,
    MinerMetadata,
    Inventory,
    Mode,
    PerformanceMetrics
)


@pytest.fixture
def mock_components():
    """Create mock Bittensor components."""
    wallet = Mock()
    wallet.hotkey.ss58_address = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"

    subtensor = Mock()
    metagraph = Mock()
    metagraph.netuid = 98
    metagraph.uids = [1, 2, 3]
    metagraph.hotkeys = [
        "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
        "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
        "5FLSigC9HGRKVhB9FiEo4Y3koPsNmBmLJbpXg2mp1hXcS59Y"
    ]

    # Mock axons
    axon1 = Mock()
    axon1.is_serving = True
    axon1.ip = "127.0.0.1"
    axon1.port = 8001

    axon2 = Mock()
    axon2.is_serving = True
    axon2.ip = "127.0.0.1"
    axon2.port = 8002

    axon3 = Mock()
    axon3.is_serving = False
    axon3.ip = "127.0.0.1"
    axon3.port = 8003

    metagraph.axons = [axon1, axon2, axon3]

    db = Mock()

    config = {
        'chain_id': 8453,
        'max_il': 0.10,
        'min_tick_width': 60,
        'max_rebalances': 4,
        'performance_weight': 0.7,
        'lp_alignment_weight': 0.3,
        'top_n_strategies': 3,
        'winning_strategy_file': 'test_winning_strategy.json',
        'postgres_access': {}
    }

    return {
        'wallet': wallet,
        'subtensor': subtensor,
        'metagraph': metagraph,
        'db': db,
        'config': config
    }


@pytest.fixture
def validator(mock_components):
    """Create a validator instance."""
    return SN98Validator(
        wallet=mock_components['wallet'],
        subtensor=mock_components['subtensor'],
        metagraph=mock_components['metagraph'],
        db=mock_components['db'],
        config=mock_components['config']
    )


def test_validator_initialization(validator, mock_components):
    """Test validator initializes correctly."""
    assert validator.wallet == mock_components['wallet']
    assert validator.subtensor == mock_components['subtensor']
    assert validator.metagraph == mock_components['metagraph']
    assert validator.db == mock_components['db']
    assert validator.backtester is not None
    assert validator.scorer is not None


def test_generate_round_request(validator):
    """Test round request generation."""
    inventory = Inventory(
        amount0="1000000000000000000",
        amount1="2500000000"
    )

    request = validator.generate_round_request(
        pair_address="0x1234567890123456789012345678901234567890",
        target_block=12345678,
        inventory=inventory,
        mode=Mode.INVENTORY
    )

    assert isinstance(request, ValidatorRequest)
    assert request.pairAddress == "0x1234567890123456789012345678901234567890"
    assert request.chainId == 8453
    assert request.target_block == 12345678
    assert request.mode == Mode.INVENTORY
    assert request.inventory == inventory
    assert request.metadata is not None
    assert request.metadata.round_id is not None
    assert request.metadata.constraints is not None


def test_query_miner_success(validator, mock_components):
    """Test successful miner query."""
    # Mock response
    mock_response_data = {
        'strategy': {
            'positions': [
                {
                    'tickLower': -10000,
                    'tickUpper': -9900,
                    'allocation0': '500000000000000000',
                    'allocation1': '1250000000',
                    'confidence': 0.9
                }
            ],
            'rebalance_rule': {
                'trigger': 'price_outside_range',
                'cooldown_blocks': 300
            }
        },
        'miner_metadata': {
            'version': '1.0.0',
            'model_info': 'test-model'
        }
    }

    request = ValidatorRequest(
        pairAddress="0x1234567890123456789012345678901234567890",
        chainId=8453,
        target_block=12345678,
        mode=Mode.INVENTORY,
        inventory=Inventory(amount0="1000000000000000000", amount1="2500000000"),
        metadata=Mock(round_id="test-001", constraints=Mock())
    )

    with patch('requests.post') as mock_post:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_response_data
        mock_post.return_value = mock_response

        response = validator.query_miner(0, request)

        assert response is not None
        assert isinstance(response, MinerResponse)
        assert len(response.strategy.positions) == 1
        assert response.miner_metadata.version == '1.0.0'


def test_query_miner_not_serving(validator, mock_components):
    """Test query to non-serving miner."""
    request = ValidatorRequest(
        pairAddress="0x1234567890123456789012345678901234567890",
        chainId=8453,
        target_block=12345678,
        mode=Mode.INVENTORY,
        inventory=Inventory(amount0="1000000000000000000", amount1="2500000000"),
        metadata=Mock(round_id="test-001", constraints=Mock())
    )

    # Query miner 2 (index 2) which is not serving
    response = validator.query_miner(2, request)

    assert response is None


def test_query_miner_timeout(validator, mock_components):
    """Test miner query timeout."""
    request = ValidatorRequest(
        pairAddress="0x1234567890123456789012345678901234567890",
        chainId=8453,
        target_block=12345678,
        mode=Mode.INVENTORY,
        inventory=Inventory(amount0="1000000000000000000", amount1="2500000000"),
        metadata=Mock(round_id="test-001", constraints=Mock())
    )

    with patch('requests.post') as mock_post:
        import requests
        mock_post.side_effect = requests.exceptions.Timeout()

        response = validator.query_miner(0, request)

        assert response is None


def test_poll_miners(validator, mock_components):
    """Test polling multiple miners."""
    request = ValidatorRequest(
        pairAddress="0x1234567890123456789012345678901234567890",
        chainId=8453,
        target_block=12345678,
        mode=Mode.INVENTORY,
        inventory=Inventory(amount0="1000000000000000000", amount1="2500000000"),
        metadata=Mock(round_id="test-001", constraints=Mock())
    )

    mock_response_data = {
        'strategy': {
            'positions': [
                {
                    'tickLower': -10000,
                    'tickUpper': -9900,
                    'allocation0': '500000000000000000',
                    'allocation1': '1250000000',
                    'confidence': 0.9
                }
            ],
            'rebalance_rule': None
        },
        'miner_metadata': {
            'version': '1.0.0',
            'model_info': 'test-model'
        }
    }

    with patch('requests.post') as mock_post:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_response_data
        mock_post.return_value = mock_response

        responses = validator.poll_miners(request)

        # Should get responses from miners 0 and 1 (serving), not 2
        assert len(responses) == 2
        assert 0 in responses
        assert 1 in responses
        assert 2 not in responses


def test_evaluate_strategies(validator, mock_components):
    """Test strategy evaluation."""
    # Setup miner responses
    miner_responses = {
        1: MinerResponse(
            strategy=Strategy(
                positions=[
                    Position(
                        tickLower=-10000,
                        tickUpper=-9900,
                        allocation0='500000000000000000',
                        allocation1='1250000000',
                        confidence=0.9
                    )
                ],
                rebalance_rule=RebalanceRule(
                    trigger='price_outside_range',
                    cooldown_blocks=300
                )
            ),
            miner_metadata=MinerMetadata(version='1.0.0', model_info='test-model-1')
        ),
        2: MinerResponse(
            strategy=Strategy(
                positions=[
                    Position(
                        tickLower=-10000,
                        tickUpper=-9800,
                        allocation0='500000000000000000',
                        allocation1='1250000000',
                        confidence=0.85
                    )
                ],
                rebalance_rule=None
            ),
            miner_metadata=MinerMetadata(version='1.0.0', model_info='test-model-2')
        )
    }

    request = validator.generate_round_request(
        pair_address="0x1234567890123456789012345678901234567890",
        target_block=12345678,
        inventory=Inventory(amount0="1000000000000000000", amount1="2500000000"),
        mode=Mode.INVENTORY
    )

    # Mock backtester
    mock_metrics = PerformanceMetrics(
        net_pnl=500.0,
        hodl_pnl=400.0,
        net_pnl_vs_hodl=100.0,
        total_fees_collected=150.0,
        impermanent_loss=0.05,
        num_rebalances=2
    )

    validator.backtester.backtest_strategy = Mock(return_value=mock_metrics)

    scores = validator.evaluate_strategies(
        miner_responses=miner_responses,
        request=request,
        start_block=12345000,
        end_block=12345678
    )

    assert len(scores) == 2
    assert all(hasattr(s, 'final_score') for s in scores)
    assert all(hasattr(s, 'performance_metrics') for s in scores)


def test_publish_scores(validator, mock_components):
    """Test score publishing to Bittensor network."""
    from validator.models import MinerScore

    scores = [
        MinerScore(
            miner_uid=1,
            miner_hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
            performance_score=0.9,
            lp_alignment_score=0.8,
            final_score=0.87,
            performance_metrics=PerformanceMetrics(
                net_pnl=500.0, hodl_pnl=400.0, net_pnl_vs_hodl=100.0,
                total_fees_collected=150.0, impermanent_loss=0.05, num_rebalances=2
            )
        ),
        MinerScore(
            miner_uid=2,
            miner_hotkey="5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
            performance_score=0.7,
            lp_alignment_score=0.6,
            final_score=0.67,
            performance_metrics=PerformanceMetrics(
                net_pnl=300.0, hodl_pnl=400.0, net_pnl_vs_hodl=-100.0,
                total_fees_collected=100.0, impermanent_loss=0.08, num_rebalances=3
            )
        )
    ]

    # Mock set_weights
    mock_components['subtensor'].set_weights = Mock()

    validator.publish_scores(scores)

    # Verify set_weights was called
    mock_components['subtensor'].set_weights.assert_called_once()


def test_publish_winning_strategy(validator, tmp_path):
    """Test publishing winning strategy to file."""
    import json

    winning_score = MinerScore(
        miner_uid=1,
        miner_hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
        performance_score=0.9,
        lp_alignment_score=0.8,
        final_score=0.87,
        performance_metrics=PerformanceMetrics(
            net_pnl=500.0, hodl_pnl=400.0, net_pnl_vs_hodl=100.0,
            total_fees_collected=150.0, impermanent_loss=0.05, num_rebalances=2
        )
    )

    winning_response = MinerResponse(
        strategy=Strategy(
            positions=[
                Position(
                    tickLower=-10000,
                    tickUpper=-9900,
                    allocation0='500000000000000000',
                    allocation1='1250000000',
                    confidence=0.9
                )
            ],
            rebalance_rule=None
        ),
        miner_metadata=MinerMetadata(version='1.0.0', model_info='winning-model')
    )

    # Use temp file
    output_file = tmp_path / "winning_strategy.json"
    validator.config['winning_strategy_file'] = str(output_file)

    validator.publish_winning_strategy(winning_score, winning_response)

    # Verify file was created
    assert output_file.exists()

    # Verify contents
    with open(output_file, 'r') as f:
        data = json.load(f)

    assert 'winner' in data
    assert data['winner']['miner_uid'] == 1
    assert data['winner']['final_score'] == 0.87
    assert 'strategy' in data
    assert 'miner_metadata' in data
