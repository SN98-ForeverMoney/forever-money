"""
Cryptographic utilities for SN98 ForeverMoney.

Includes SS58 address encoding/decoding for Bittensor/Polkadot accounts.
"""
import hashlib

_B58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58decode(s: str) -> bytes:
    """Decode a base58 encoded string to bytes."""
    num = 0
    for c in s.encode():
        num *= 58
        num += _B58_ALPHABET.index(c)

    out = num.to_bytes((num.bit_length() + 7) // 8, "big")

    # handle leading zeros
    pad = 0
    for c in s:
        if c == "1":
            pad += 1
        else:
            break

    return b"\x00" * pad + out


def ss58_to_bytes32(ss58: str) -> bytes:
    """
    Decode SS58 address to raw AccountId32 (32 bytes).

    Works for Bittensor / Polkadot-style accounts.

    Args:
        ss58: SS58 encoded address string

    Returns:
        32-byte AccountId

    Raises:
        ValueError: If the address is invalid or checksum fails
    """
    data = _b58decode(ss58)

    if len(data) < 35:
        raise ValueError("Invalid SS58 address length")

    # SS58 prefix can be 1 or 2 bytes
    if data[0] & 0b01000000:
        prefix_len = 2
    else:
        prefix_len = 1

    prefix = data[:prefix_len]
    account_id = data[prefix_len:prefix_len + 32]
    checksum = data[prefix_len + 32:prefix_len + 34]

    if len(account_id) != 32:
        raise ValueError("Invalid AccountId length")

    # checksum = first 2 bytes of blake2b("SS58PRE" + data_without_checksum)
    h = hashlib.blake2b(b"SS58PRE" + prefix + account_id, digest_size=64).digest()
    if checksum != h[:2]:
        raise ValueError("Invalid SS58 checksum")

    return account_id


def is_valid_ss58(ss58: str) -> bool:
    """
    Check if a string is a valid SS58 address.

    Args:
        ss58: String to check

    Returns:
        True if valid SS58, False otherwise
    """
    try:
        ss58_to_bytes32(ss58)
        return True
    except (ValueError, IndexError):
        return False
