"""
Tests for the Backtester class.
"""
import pytest
from unittest.mock import Mock, MagicMock
from validator.backtester import Backtester, UniswapV3Math
from validator.models import Strategy, Position, RebalanceRule
from validator.database import PoolDataDB


@pytest.fixture
def mock_db():
    """Create a mock database."""
    db = Mock(spec=PoolDataDB)
    return db


@pytest.fixture
def backtester(mock_db):
    """Create a backtester instance."""
    return Backtester(mock_db)


def test_uniswap_math_sqrt_ratio():
    """Test sqrtPriceX96 calculations."""
    math = UniswapV3Math()

    # Test tick 0 (price = 1)
    sqrt_ratio = math.get_sqrt_ratio_at_tick(0)
    assert sqrt_ratio == 2 ** 96

    # Test negative tick
    sqrt_ratio_neg = math.get_sqrt_ratio_at_tick(-1000)
    assert sqrt_ratio_neg < 2 ** 96

    # Test positive tick
    sqrt_ratio_pos = math.get_sqrt_ratio_at_tick(1000)
    assert sqrt_ratio_pos > 2 ** 96


def test_backtester_hodl_baseline(backtester, mock_db):
    """Test HODL baseline calculation."""
    # Mock price data
    mock_db.get_price_at_block.side_effect = [2500.0, 2600.0]  # start, end

    initial_amount0 = 1000000000000000000  # 1 ETH
    initial_amount1 = 2500000000  # 2500 USDC

    hodl_pnl = backtester.calculate_hodl_baseline(
        pair_address="0x123",
        initial_amount0=initial_amount0,
        initial_amount1=initial_amount1,
        start_block=100,
        end_block=200
    )

    # Should show positive return due to price increase
    assert hodl_pnl > 0


def test_backtester_strategy_simulation(backtester, mock_db):
    """Test full strategy backtesting."""
    # Setup mock data - need enough values for all calls
    # backtest_strategy calls: start price, end price, HODL start, HODL end
    # simulate_position calls: final price for each position
    mock_db.get_price_at_block.return_value = 2500.0  # Use return_value for consistent price
    mock_db.get_swap_events.return_value = [
        {
            'block_number': 150,
            'amount0': '-1000000000000000',
            'amount1': '2500000',
            'sqrt_price_x96': str(int(2500**0.5 * 2**96)),  # ~$2500 price
            'tick': -8000,
            'liquidity': '10000000000000000000'  # Pool liquidity
        }
    ]

    # Create test strategy
    strategy = Strategy(
        positions=[
            Position(
                tickLower=-10000,
                tickUpper=-8000,
                allocation0="500000000000000000",
                allocation1="1250000000",
                confidence=0.85
            ),
            Position(
                tickLower=-8000,
                tickUpper=-6000,
                allocation0="500000000000000000",
                allocation1="1250000000",
                confidence=0.75
            )
        ],
        rebalance_rule=RebalanceRule(
            trigger="price_outside_range",
            cooldown_blocks=300
        )
    )

    # Run backtest
    metrics = backtester.backtest_strategy(
        pair_address="0x123",
        strategy=strategy,
        initial_amount0=1000000000000000000,
        initial_amount1=2500000000,
        start_block=100,
        end_block=200
    )

    # Verify metrics structure
    assert metrics.net_pnl is not None
    assert metrics.hodl_pnl is not None
    assert metrics.total_fees_collected >= 0
    assert metrics.impermanent_loss >= 0
    assert metrics.num_rebalances >= 0


def test_position_simulation_in_range(backtester, mock_db):
    """Test position simulation when price stays in range."""
    mock_db.get_swap_events.return_value = []
    mock_db.get_price_at_block.return_value = 2500.0

    position = Position(
        tickLower=-10000,
        tickUpper=-8000,
        allocation0="1000000000000000000",
        allocation1="2500000000",
        confidence=0.9
    )

    result = backtester.simulate_position(
        pair_address="0x123",
        position=position,
        start_block=100,
        end_block=200,
        current_price=2500.0
    )

    assert result['fees_collected'] >= 0
    assert result['impermanent_loss'] >= 0


def test_position_simulation_out_of_range(backtester, mock_db):
    """Test position simulation when price moves out of range."""
    mock_db.get_swap_events.return_value = []
    mock_db.get_price_at_block.side_effect = [2500.0, 3000.0]  # Large price movement

    position = Position(
        tickLower=-10000,
        tickUpper=-9500,  # Narrow range
        allocation0="1000000000000000000",
        allocation1="2500000000",
        confidence=0.9
    )

    result = backtester.simulate_position(
        pair_address="0x123",
        position=position,
        start_block=100,
        end_block=200,
        current_price=2500.0
    )

    # Out of range positions should collect fewer fees
    assert result['fees_collected'] >= 0
