
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from web3 import Web3

from validator.services.liqmanager import SnLiqManagerService
from validator.utils.web3 import ZERO_ADDRESS
from protocol import Inventory

# Constants for testing - Use valid hex addresses
CHAIN_ID = 8453
LIQ_MANAGER_ADDR = "0x1234567890123456789012345678901234567890"
POOL_ADDR = "0x2234567890123456789012345678901234567890"
TOKEN0 = "0x3234567890123456789012345678901234567890"
TOKEN1 = "0x4234567890123456789012345678901234567890"
AK_ADDR = "0x5234567890123456789012345678901234567890"
POS_MANAGER_ADDR = "0x6234567890123456789012345678901234567890"
NFT_MANAGER_ADDR = "0x7234567890123456789012345678901234567890"
POOL_MANAGER_ADDR = "0x8234567890123456789012345678901234567890"

@pytest.fixture
def mock_web3_helper():
    with patch("validator.services.liqmanager.AsyncWeb3Helper") as mock:
        yield mock

@pytest.fixture
def service(mock_web3_helper):
    # Setup default mock behavior
    mock_web3 = mock_web3_helper.make_web3.return_value
    
    # Mock contracts as MagicMock so attribute access works normally
    liq_contract = MagicMock()
    pool_contract = MagicMock()
    
    # Default contract creation behavior
    def make_contract_side_effect(name, addr):
        if name == "LiquidityManager":
            return liq_contract
        if name == "ICLPool":
            return pool_contract
        return MagicMock()

    mock_web3.make_contract_by_name.side_effect = make_contract_side_effect
    
    service = SnLiqManagerService(CHAIN_ID, LIQ_MANAGER_ADDR, POOL_ADDR)
    # Assign the mocks directly (the __init__ call already did this via make_contract_by_name)
    # But we want to hold references to them for configuring return values
    service.liq_manager = liq_contract
    service.pool = pool_contract
    return service

def mock_contract_call(contract_function_mock, return_value):
    """Helper to mock a contract function call: contract.functions.func().call() -> return_value"""
    # contract.functions.func() returns a method object
    method_obj = MagicMock()
    contract_function_mock.return_value = method_obj
    # method_obj.call() returns an awaitable (AsyncMock)
    method_obj.call = AsyncMock(return_value=return_value)
    return method_obj.call

@pytest.mark.asyncio
async def test_get_pool_tokens_success(service):
    # Setup
    mock_contract_call(service.pool.functions.token0, TOKEN0)
    mock_contract_call(service.pool.functions.token1, TOKEN1)
    
    # Execute
    t0, t1 = await service._get_pool_tokens()
    
    # Verify
    assert t0 == TOKEN0
    assert t1 == TOKEN1

@pytest.mark.asyncio
async def test_get_pool_tokens_failure(service):
    # Setup
    # Mock the .call() to raise exception
    method_obj = MagicMock()
    service.pool.functions.token0.return_value = method_obj
    method_obj.call = AsyncMock(side_effect=Exception("RPC Error"))
    
    # Execute & Verify
    with pytest.raises(ValueError, match="Failed to extract tokens"):
        await service._get_pool_tokens()

@pytest.mark.asyncio
async def test_find_registered_ak_found(service):
    # Setup
    mock_contract_call(service.liq_manager.functions.akAddressToPoolManager, POOL_MANAGER_ADDR)
    
    # Execute
    result = await service._find_registered_ak(TOKEN0)
    
    # Verify
    assert result == TOKEN0

@pytest.mark.asyncio
async def test_find_registered_ak_not_found(service):
    # Setup
    mock_contract_call(service.liq_manager.functions.akAddressToPoolManager, ZERO_ADDRESS)
    
    # Execute
    result = await service._find_registered_ak(TOKEN0)
    
    # Verify
    assert result is None

@pytest.mark.asyncio
async def test_find_registered_ak_error(service):
    # Setup
    method_obj = MagicMock()
    service.liq_manager.functions.akAddressToPoolManager.return_value = method_obj
    method_obj.call = AsyncMock(side_effect=Exception("Revert"))
    
    # Execute
    result = await service._find_registered_ak(TOKEN0)
    
    # Verify
    assert result is None

@pytest.mark.asyncio
async def test_get_inventory_success_token0_ak(service):
    # Setup
    mock_contract_call(service.pool.functions.token0, TOKEN0)
    mock_contract_call(service.pool.functions.token1, TOKEN1)
    
    # Mock registration: Token0 is AK
    mock_contract_call(service.liq_manager.functions.akAddressToPoolManager, POOL_MANAGER_ADDR)
    
    # Mock stash
    # akToStashedTokens is called with (ak, token).
    # We need side_effect on the call() AsyncMock
    method_obj = MagicMock()
    service.liq_manager.functions.akToStashedTokens.return_value = method_obj
    
    async def stashed_side_effect(*args, **kwargs):
        # We can't easily get arguments passed to akToStashedTokens here because
        # call() is called on the result of akToStashedTokens(arg1, arg2).
        # But we can assume the flow calls it correctly.
        # Wait, the structure is: await contract.functions.func(args).call()
        # So 'func' is called with args, returns 'method_obj'. Then 'method_obj.call()' is awaited.
        # If we want return value to depend on args passed to 'func', we need 'func' side_effect.
        return 0
    
    # Better approach for dependent returns:
    def ak_to_stashed_tokens_side_effect(ak, token):
        method = MagicMock()
        val = 0
        if token == Web3.to_checksum_address(TOKEN0): val = 1000
        if token == Web3.to_checksum_address(TOKEN1): val = 2000
        method.call = AsyncMock(return_value=val)
        return method

    service.liq_manager.functions.akToStashedTokens.side_effect = ak_to_stashed_tokens_side_effect

    # Execute
    inventory = await service.get_inventory()
    
    # Verify
    assert inventory.amount0 == "1000"
    assert inventory.amount1 == "2000"

@pytest.mark.asyncio
async def test_get_inventory_success_token1_ak(service):
    # Setup
    mock_contract_call(service.pool.functions.token0, TOKEN0)
    mock_contract_call(service.pool.functions.token1, TOKEN1)
    
    # Mock registration: Token0 returns ZERO, Token1 returns Address
    def ak_lookup_side_effect(addr):
        method = MagicMock()
        if addr == Web3.to_checksum_address(TOKEN1):
            method.call = AsyncMock(return_value=POOL_MANAGER_ADDR)
        else:
            method.call = AsyncMock(return_value=ZERO_ADDRESS)
        return method
        
    service.liq_manager.functions.akAddressToPoolManager.side_effect = ak_lookup_side_effect

    # Mock stash
    mock_contract_call(service.liq_manager.functions.akToStashedTokens, 500)

    # Execute
    inventory = await service.get_inventory()
    
    # Verify
    assert inventory.amount0 == "500"
    assert inventory.amount1 == "500"

@pytest.mark.asyncio
async def test_get_inventory_failure_no_ak(service):
    # Setup
    mock_contract_call(service.pool.functions.token0, TOKEN0)
    mock_contract_call(service.pool.functions.token1, TOKEN1)
    
    # Mock registration: Both return ZERO
    mock_contract_call(service.liq_manager.functions.akAddressToPoolManager, ZERO_ADDRESS)

    # Execute & Verify
    with pytest.raises(SystemExit, match="Neither token0"):
        await service.get_inventory()

@pytest.mark.asyncio
async def test_get_current_positions_success(service, mock_web3_helper):
    # Setup
    mock_contract_call(service.pool.functions.token0, TOKEN0)
    mock_contract_call(service.pool.functions.token1, TOKEN1)
    mock_contract_call(service.pool.functions.slot0, [79228162514264337593543950336]) # Price 1.0
    
    # Mock Position Manager lookup: Token0 -> PosManager
    def ak_pos_lookup(addr):
        method = MagicMock()
        if addr == Web3.to_checksum_address(TOKEN0):
            method.call = AsyncMock(return_value=POS_MANAGER_ADDR)
        else:
            method.call = AsyncMock(return_value=ZERO_ADDRESS)
        return method
    service.liq_manager.functions.akAddressToPositionManager.side_effect = ak_pos_lookup
    
    # Mock PositionManager contract
    pos_contract = MagicMock()
    mock_contract_call(pos_contract.functions.tokenIds, [1, 2])
    mock_contract_call(pos_contract.functions.nftManager, NFT_MANAGER_ADDR)
    
    # Mock NFTManager contract
    nft_contract = MagicMock()
    
    def positions_side_effect(token_id):
        method = MagicMock()
        # Position info: (nonce, operator, token0, token1, tickSpacing, tickLower, tickUpper, liquidity, ...)
        if token_id == 1:
            method.call = AsyncMock(return_value=[0, ZERO_ADDRESS, TOKEN0, TOKEN1, 60, -100, 100, 1000000, 0, 0, 0, 0])
        elif token_id == 2:
            method.call = AsyncMock(return_value=[0, ZERO_ADDRESS, TOKEN0, TOKEN1, 60, -200, 200, 500000, 0, 0, 0, 0])
        return method

    nft_contract.functions.positions.side_effect = positions_side_effect

    # Setup factory to return these contracts
    mock_web3 = mock_web3_helper.make_web3.return_value
    def make_contract_side_effect(name, addr):
        if name == "AeroCLPositionManager" and addr == POS_MANAGER_ADDR:
            return pos_contract
        if name == "INonfungiblePositionManager" and addr == NFT_MANAGER_ADDR:
            return nft_contract
        # Default fallback
        return MagicMock()
    mock_web3.make_contract_by_name.side_effect = make_contract_side_effect

    # Execute
    positions = await service.get_current_positions()
    
    # Verify
    assert len(positions) == 2
    assert positions[0].tick_lower == -100
    assert positions[0].tick_upper == 100
    assert int(positions[0].allocation0) > 0 
    
    assert positions[1].tick_lower == -200
    assert positions[1].tick_upper == 200

@pytest.mark.asyncio
async def test_get_current_positions_no_tokens(service, mock_web3_helper):
    # Setup
    mock_contract_call(service.pool.functions.token0, TOKEN0)
    mock_contract_call(service.pool.functions.token1, TOKEN1)
    mock_contract_call(service.pool.functions.slot0, [79228162514264337593543950336])
    
    # Mock Position Manager lookup: Token0 -> PosManager
    def ak_pos_lookup(addr):
        method = MagicMock()
        if addr == Web3.to_checksum_address(TOKEN0):
            method.call = AsyncMock(return_value=POS_MANAGER_ADDR)
        else:
            method.call = AsyncMock(return_value=ZERO_ADDRESS)
        return method
    service.liq_manager.functions.akAddressToPositionManager.side_effect = ak_pos_lookup
    
    # Mock Empty token IDs
    pos_contract = MagicMock()
    mock_contract_call(pos_contract.functions.tokenIds, [])
    
    # Inject via factory
    mock_web3 = mock_web3_helper.make_web3.return_value
    mock_web3.make_contract_by_name.return_value = pos_contract

    # Execute
    positions = await service.get_current_positions()
    
    # Verify
    assert positions == []

@pytest.mark.asyncio
async def test_get_current_positions_invalid_vault(service):
    # Setup: Both tokens have position manager
    mock_contract_call(service.pool.functions.token0, TOKEN0)
    mock_contract_call(service.pool.functions.token1, TOKEN1)
    
    mock_contract_call(service.liq_manager.functions.akAddressToPositionManager, POS_MANAGER_ADDR)

    # Execute & Verify
    with pytest.raises(ValueError, match="Invalid vault"):
        await service.get_current_positions()

@pytest.mark.asyncio
async def test_get_current_positions_no_manager(service):
    # Setup: Neither token has position manager
    mock_contract_call(service.pool.functions.token0, TOKEN0)
    mock_contract_call(service.pool.functions.token1, TOKEN1)
    
    mock_contract_call(service.liq_manager.functions.akAddressToPositionManager, ZERO_ADDRESS)

    # Execute & Verify
    with pytest.raises(ValueError, match="Neither token0"):
        await service.get_current_positions()
