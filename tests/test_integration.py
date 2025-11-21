"""
End-to-end integration tests for SN98.

These tests verify the complete flow from validator to miner and back.
"""
import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from validator.models import (
    ValidatorRequest,
    MinerResponse,
    Inventory,
    Mode
)
from miner.strategy import SimpleStrategyGenerator


def test_miner_strategy_generation():
    """Test that miner can generate a valid strategy from validator request."""
    # Create a validator request
    request = ValidatorRequest(
        pairAddress="0x1234567890123456789012345678901234567890",
        chainId=8453,
        target_block=12345678,
        mode=Mode.INVENTORY,
        inventory=Inventory(
            amount0="1000000000000000000",
            amount1="2500000000"
        ),
        metadata=Mock(
            round_id="test-round-001",
            constraints=Mock(
                max_il=0.10,
                min_tick_width=60,
                max_rebalances=4
            )
        ),
        postgres_access=None
    )

    # Generate strategy
    generator = SimpleStrategyGenerator()
    strategy = generator.generate_strategy(request)

    # Verify strategy structure
    assert strategy is not None
    assert len(strategy.positions) > 0
    assert strategy.rebalance_rule is not None

    # Verify positions meet constraints
    for position in strategy.positions:
        tick_width = position.tickUpper - position.tickLower
        assert tick_width >= 60  # min_tick_width


def test_miner_endpoint_request_response(tmp_path):
    """Test full request-response cycle with miner endpoint."""
    from miner.miner import app

    client = app.test_client()

    # Test health check
    response = client.get('/health')
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'healthy'

    # Test strategy prediction
    request_data = {
        "pairAddress": "0x1234567890123456789012345678901234567890",
        "chainId": 8453,
        "target_block": 12345678,
        "mode": "inventory",
        "inventory": {
            "amount0": "1000000000000000000",
            "amount1": "2500000000"
        },
        "current_positions": [],
        "metadata": {
            "round_id": "test-round-001",
            "constraints": {
                "max_il": 0.10,
                "min_tick_width": 60,
                "max_rebalances": 4
            }
        }
    }

    response = client.post(
        '/predict_strategy',
        data=json.dumps(request_data),
        content_type='application/json'
    )

    assert response.status_code == 200
    data = response.get_json()

    # Verify response structure
    assert 'strategy' in data
    assert 'miner_metadata' in data
    assert 'positions' in data['strategy']
    assert len(data['strategy']['positions']) > 0
    assert 'version' in data['miner_metadata']
    assert 'model_info' in data['miner_metadata']

    # Verify positions
    for position in data['strategy']['positions']:
        assert 'tickLower' in position
        assert 'tickUpper' in position
        assert 'allocation0' in position
        assert 'allocation1' in position
        tick_width = position['tickUpper'] - position['tickLower']
        assert tick_width >= 60


def test_miner_endpoint_invalid_request():
    """Test miner endpoint with invalid request."""
    from miner.miner import app

    client = app.test_client()

    # Missing required fields
    request_data = {
        "pairAddress": "0x1234567890123456789012345678901234567890",
        "target_block": 12345678
        # Missing inventory and mode
    }

    response = client.post(
        '/predict_strategy',
        data=json.dumps(request_data),
        content_type='application/json'
    )

    assert response.status_code == 400


def test_validator_to_miner_integration():
    """Test complete validator-miner interaction flow."""
    from validator.validator import SN98Validator
    from validator.database import PoolDataDB

    # Setup mock components
    wallet = Mock()
    wallet.hotkey.ss58_address = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"

    subtensor = Mock()
    metagraph = Mock()
    metagraph.netuid = 98
    metagraph.uids = [1]
    metagraph.hotkeys = ["5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"]

    # Mock serving axon
    axon = Mock()
    axon.is_serving = True
    axon.ip = "127.0.0.1"
    axon.port = 8000
    metagraph.axons = [axon]

    db = Mock(spec=PoolDataDB)

    config = {
        'chain_id': 8453,
        'max_il': 0.10,
        'min_tick_width': 60,
        'max_rebalances': 4,
        'performance_weight': 0.7,
        'lp_alignment_weight': 0.3,
        'top_n_strategies': 3,
        'postgres_access': {}
    }

    validator = SN98Validator(
        wallet=wallet,
        subtensor=subtensor,
        metagraph=metagraph,
        db=db,
        config=config
    )

    # Create request
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

    # Verify request structure
    assert request.pairAddress == "0x1234567890123456789012345678901234567890"
    assert request.chainId == 8453
    assert request.mode == Mode.INVENTORY
    assert request.inventory.amount0 == "1000000000000000000"
    assert request.inventory.amount1 == "2500000000"

    # Mock miner response
    mock_response_data = {
        'strategy': {
            'positions': [
                {
                    'tickLower': -10000,
                    'tickUpper': -9900,
                    'allocation0': '500000000000000000',
                    'allocation1': '1250000000',
                    'confidence': 0.9
                },
                {
                    'tickLower': -9900,
                    'tickUpper': -9300,
                    'allocation0': '500000000000000000',
                    'allocation1': '1250000000',
                    'confidence': 0.8
                }
            ],
            'rebalance_rule': {
                'trigger': 'price_outside_range',
                'cooldown_blocks': 300
            }
        },
        'miner_metadata': {
            'version': '1.0.0',
            'model_info': 'simple-rule-based'
        }
    }

    with patch('requests.post') as mock_post:
        mock_http_response = Mock()
        mock_http_response.status_code = 200
        mock_http_response.json.return_value = mock_response_data
        mock_post.return_value = mock_http_response

        # Query miner
        miner_response = validator.query_miner(0, request)

        assert miner_response is not None
        assert isinstance(miner_response, MinerResponse)
        assert len(miner_response.strategy.positions) == 2

        # Verify positions meet constraints
        for position in miner_response.strategy.positions:
            tick_width = position.tickUpper - position.tickLower
            assert tick_width >= config['min_tick_width']


def test_constraint_validation_in_evaluation():
    """Test that constraint validation works during strategy evaluation."""
    from validator.validator import SN98Validator
    from validator.models import (
        Strategy, Position, RebalanceRule, MinerMetadata,
        MinerResponse, PerformanceMetrics
    )

    # Setup validator
    wallet = Mock()
    wallet.hotkey.ss58_address = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"

    subtensor = Mock()
    metagraph = Mock()
    metagraph.netuid = 98
    metagraph.uids = [1, 2]
    metagraph.hotkeys = [
        "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
        "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
    ]

    db = Mock()

    config = {
        'chain_id': 8453,
        'max_il': 0.10,
        'min_tick_width': 60,
        'max_rebalances': 4,
        'performance_weight': 0.7,
        'lp_alignment_weight': 0.3,
        'top_n_strategies': 3,
        'postgres_access': {}
    }

    validator = SN98Validator(
        wallet=wallet,
        subtensor=subtensor,
        metagraph=metagraph,
        db=db,
        config=config
    )

    # Create one valid and one invalid strategy
    miner_responses = {
        1: MinerResponse(
            strategy=Strategy(
                positions=[
                    Position(
                        tickLower=-10000,
                        tickUpper=-9900,  # Valid width: 100
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
            miner_metadata=MinerMetadata(version='1.0.0', model_info='valid-miner')
        ),
        2: MinerResponse(
            strategy=Strategy(
                positions=[
                    Position(
                        tickLower=-10000,
                        tickUpper=-9970,  # Invalid width: 30 < 60
                        allocation0='500000000000000000',
                        allocation1='1250000000',
                        confidence=0.85
                    )
                ],
                rebalance_rule=None
            ),
            miner_metadata=MinerMetadata(version='1.0.0', model_info='invalid-miner')
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

    # Evaluate strategies
    scores = validator.evaluate_strategies(
        miner_responses=miner_responses,
        request=request,
        start_block=12345000,
        end_block=12345678
    )

    # Miner 2 should have zero score due to constraint violation
    miner_2_score = next(s for s in scores if s.miner_uid == 2)
    assert miner_2_score.final_score == 0.0
    assert len(miner_2_score.constraint_violations) > 0

    # Miner 1 should have positive score
    miner_1_score = next(s for s in scores if s.miner_uid == 1)
    assert miner_1_score.final_score > 0.0
    assert len(miner_1_score.constraint_violations) == 0


def test_api_format_compatibility():
    """Test that API format matches specification exactly."""
    # This test ensures our models match the spec.md API format

    # Test ValidatorRequest format
    request = ValidatorRequest(
        pairAddress="0x0000000000000000000000000000000000000000",
        chainId=8453,
        target_block=12345678,
        mode=Mode.INVENTORY,
        inventory=Inventory(
            amount0="1000000000000000000",
            amount1="2500000000"
        ),
        current_positions=[],
        metadata=Mock(
            round_id="2025-02-01-001",
            constraints=Mock(
                max_il=0.10,
                min_tick_width=60,
                max_rebalances=4
            )
        ),
        postgres_access=None
    )

    # Serialize to dict
    request_dict = request.model_dump()

    # Verify all required fields exist
    assert 'pairAddress' in request_dict
    assert 'chainId' in request_dict
    assert 'target_block' in request_dict
    assert 'mode' in request_dict
    assert 'inventory' in request_dict
    assert 'metadata' in request_dict

    # Test MinerResponse format
    from validator.models import Strategy, Position, RebalanceRule, MinerMetadata

    response = MinerResponse(
        strategy=Strategy(
            positions=[
                Position(
                    tickLower=-9600,
                    tickUpper=-8400,
                    allocation0="500000000000000000",
                    allocation1="0",
                    confidence=0.82
                ),
                Position(
                    tickLower=-8400,
                    tickUpper=-7200,
                    allocation0="0",
                    allocation1="1800000000",
                    confidence=0.78
                )
            ],
            rebalance_rule=RebalanceRule(
                trigger="price_outside_range",
                cooldown_blocks=300
            )
        ),
        miner_metadata=MinerMetadata(
            version="1.0.0",
            model_info="lstm-v3-swaps-optimized"
        )
    )

    # Serialize to dict
    response_dict = response.model_dump()

    # Verify all required fields exist
    assert 'strategy' in response_dict
    assert 'positions' in response_dict['strategy']
    assert 'miner_metadata' in response_dict
    assert 'version' in response_dict['miner_metadata']
    assert 'model_info' in response_dict['miner_metadata']

    # Verify position structure
    for pos in response_dict['strategy']['positions']:
        assert 'tickLower' in pos
        assert 'tickUpper' in pos
        assert 'allocation0' in pos
        assert 'allocation1' in pos
        assert 'confidence' in pos
