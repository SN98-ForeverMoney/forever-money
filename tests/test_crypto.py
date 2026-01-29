"""
Tests for cryptographic utilities (SS58 encoding/decoding).
"""
import pytest

from validator.utils.crypto import ss58_to_bytes32, is_valid_ss58, _b58decode


class TestBase58Decode:
    """Tests for base58 decoding."""

    def test_b58decode_simple(self):
        """Test basic base58 decoding."""
        # "1" in base58 is 0x00
        result = _b58decode("1")
        assert result == b"\x00"

    def test_b58decode_leading_ones(self):
        """Test base58 decoding with leading 1s (zeros)."""
        # Multiple leading 1s should produce leading zero bytes
        result = _b58decode("111")
        assert result == b"\x00\x00\x00"


class TestSS58ToBytes32:
    """Tests for SS58 to bytes32 conversion."""

    def test_alice_address(self):
        """Test decoding Alice's well-known SS58 address."""
        alice_ss58 = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
        result = ss58_to_bytes32(alice_ss58)

        # Known AccountId32 for Alice
        expected = bytes.fromhex(
            "d43593c715fdd31c61141abd04a99fd6822c8558854ccde39a5684e7a56da27d"
        )
        assert result == expected
        assert len(result) == 32

    def test_bob_address(self):
        """Test decoding Bob's well-known SS58 address."""
        bob_ss58 = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
        result = ss58_to_bytes32(bob_ss58)

        # Known AccountId32 for Bob
        expected = bytes.fromhex(
            "8eaf04151687736326c9fea17e25fc5287613693c912909cb226aa4794f26a48"
        )
        assert result == expected
        assert len(result) == 32

    def test_charlie_address(self):
        """Test decoding Charlie's well-known SS58 address."""
        charlie_ss58 = "5FLSigC9HGRKVhB9FiEo4Y3koPsNmBmLJbpXg2mp1hXcS59Y"
        result = ss58_to_bytes32(charlie_ss58)

        # Known AccountId32 for Charlie
        expected = bytes.fromhex(
            "90b5ab205c6974c9ea841be688864633dc9ca8a357843eeacf2314649965fe22"
        )
        assert result == expected
        assert len(result) == 32

    def test_invalid_too_short(self):
        """Test that short addresses raise ValueError."""
        with pytest.raises(ValueError, match="Invalid SS58 address length"):
            ss58_to_bytes32("abc")

    def test_invalid_checksum(self):
        """Test that invalid checksum raises ValueError."""
        # Alter the last character to break the checksum
        invalid_ss58 = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQZ"
        with pytest.raises(ValueError, match="Invalid SS58 checksum"):
            ss58_to_bytes32(invalid_ss58)

    def test_invalid_characters(self):
        """Test that invalid base58 characters raise an error."""
        # 'O', 'I', 'l', '0' are not valid base58 characters
        with pytest.raises(ValueError):
            ss58_to_bytes32("5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutO0")

    def test_output_is_bytes(self):
        """Test that output is bytes type."""
        alice_ss58 = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
        result = ss58_to_bytes32(alice_ss58)
        assert isinstance(result, bytes)

    def test_output_length_always_32(self):
        """Test that output is always exactly 32 bytes."""
        addresses = [
            "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",  # Alice
            "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",  # Bob
            "5FLSigC9HGRKVhB9FiEo4Y3koPsNmBmLJbpXg2mp1hXcS59Y",  # Charlie
        ]
        for addr in addresses:
            result = ss58_to_bytes32(addr)
            assert len(result) == 32, f"Address {addr} produced {len(result)} bytes"


class TestIsValidSS58:
    """Tests for SS58 address validation."""

    def test_valid_addresses(self):
        """Test that valid SS58 addresses return True."""
        valid_addresses = [
            "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",  # Alice
            "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",  # Bob
            "5FLSigC9HGRKVhB9FiEo4Y3koPsNmBmLJbpXg2mp1hXcS59Y",  # Charlie
        ]
        for addr in valid_addresses:
            assert is_valid_ss58(addr) is True, f"Expected {addr} to be valid"

    def test_invalid_empty_string(self):
        """Test that empty string returns False."""
        assert is_valid_ss58("") is False

    def test_invalid_short_string(self):
        """Test that short strings return False."""
        assert is_valid_ss58("abc") is False
        assert is_valid_ss58("5Grw") is False

    def test_invalid_hex_address(self):
        """Test that hex addresses return False."""
        assert is_valid_ss58("0x1234567890abcdef") is False
        assert is_valid_ss58("0xd43593c715fdd31c61141abd04a99fd6822c8558") is False

    def test_invalid_ethereum_address(self):
        """Test that Ethereum addresses return False."""
        assert is_valid_ss58("0x742d35Cc6634C0532925a3b844Bc9e7595f8fE00") is False

    def test_invalid_random_string(self):
        """Test that random strings return False."""
        assert is_valid_ss58("invalid") is False
        assert is_valid_ss58("hello world") is False
        assert is_valid_ss58("not_an_address_at_all") is False

    def test_invalid_bad_checksum(self):
        """Test that addresses with bad checksums return False."""
        # Valid address with last char changed
        assert is_valid_ss58("5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQZ") is False

    def test_returns_boolean(self):
        """Test that function returns boolean type."""
        result_valid = is_valid_ss58("5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY")
        result_invalid = is_valid_ss58("invalid")

        assert isinstance(result_valid, bool)
        assert isinstance(result_invalid, bool)


class TestSS58Integration:
    """Integration tests for SS58 utilities."""

    def test_roundtrip_hex_comparison(self):
        """Test that decoded bytes can be compared as hex strings."""
        alice_ss58 = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
        expected_hex = "d43593c715fdd31c61141abd04a99fd6822c8558854ccde39a5684e7a56da27d"

        result = ss58_to_bytes32(alice_ss58)
        result_hex = result.hex()

        assert result_hex == expected_hex
        assert result_hex.lower() == expected_hex.lower()

    def test_case_insensitive_hex_comparison(self):
        """Test hex comparison is case insensitive."""
        alice_ss58 = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"

        result = ss58_to_bytes32(alice_ss58)
        result_hex = result.hex()

        # Should match regardless of case
        upper_hex = "D43593C715FDD31C61141ABD04A99FD6822C8558854CCDE39A5684E7A56DA27D"
        assert result_hex.lower() == upper_hex.lower()

    def test_validation_before_decode(self):
        """Test using is_valid_ss58 before decoding."""
        addresses = [
            ("5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY", True),
            ("invalid", False),
            ("0x1234", False),
        ]

        for addr, expected_valid in addresses:
            is_valid = is_valid_ss58(addr)
            assert is_valid == expected_valid

            if is_valid:
                # Should not raise
                result = ss58_to_bytes32(addr)
                assert len(result) == 32
