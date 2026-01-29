import os
from typing import TypeVar, Type, Optional

from dotenv import load_dotenv

load_dotenv()

T = TypeVar("T")


def get_env_variable(name: str, type_: Type[T], default: Optional[T]) -> T:
    """Type-safe wrapper for `os.getenv`.

    Args:
        name (str): Name of the environment variable.
        type_ (Type[T]): Type of the environment variable.
        default (T): Default value if the environment variable is not set.

    Returns:
        T: Value of the environment variable.

    Usage:
        ```python
        from source.helpers.dotenv import get_env_variable

        # Get a string environment variable with a default value.
        get_env_variable("ENVIRONMENT", "development", str)

        # Get an integer environment variable with a default value.
        get_env_variable("PORT", 8000, int)
        ```
    """

    try:
        value = os.getenv(name, default)
        return type_.__call__(value)
    except ValueError:
        raise ValueError(
            f"Environment variable '{name}' is not of type '{type_.__name__}'."
        )
    except TypeError:
        raise TypeError(
            f"Environment variable '{name}' is not set and has no default value."
        )

MAINNET_RPC = get_env_variable(
    name="MAINNET_RPC",
    type_=str,
    default="https://eth.llamarpc.com",
)
BASE_RPC = get_env_variable(
    name="BASE_RPC",
    type_=str,
    default="https://base.llamarpc.com",
)

# Validator configuration
NETUID = get_env_variable(
    name="NETUID",
    type_=int,
    default=98,
)
SUBTENSOR_NETWORK = get_env_variable(
    name="SUBTENSOR_NETWORK",
    type_=str,
    default="finney",
)
EXECUTOR_BOT_URL = get_env_variable(
    name="EXECUTOR_BOT_URL",
    type_=str,
    default=None,
)
EXECUTOR_BOT_API_KEY = get_env_variable(
    name="EXECUTOR_BOT_API_KEY",
    type_=str,
    default=None,
)
REBALANCE_CHECK_INTERVAL = get_env_variable(
    name="REBALANCE_CHECK_INTERVAL",
    type_=int,
    default=100,
)
PROFIT_RATIO = get_env_variable(
    name="PROFIT_RATIO",
    type_=float,
    default=1.0,
)

# Database configuration
JOBS_POSTGRES_HOST = get_env_variable(
    name="JOBS_POSTGRES_HOST",
    type_=str,
    default="localhost",
)
JOBS_POSTGRES_PORT = get_env_variable(
    name="JOBS_POSTGRES_PORT",
    type_=int,
    default=5432,
)
JOBS_POSTGRES_DB = get_env_variable(
    name="JOBS_POSTGRES_DB",
    type_=str,
    default="sn98_jobs",
)
JOBS_POSTGRES_USER = get_env_variable(
    name="JOBS_POSTGRES_USER",
    type_=str,
    default="sn98_user",
)
JOBS_POSTGRES_PASSWORD = get_env_variable(
    name="JOBS_POSTGRES_PASSWORD",
    type_=str,
    default="",
)

# Miner configuration
MINER_VERSION = get_env_variable(
    name="MINER_VERSION",
    type_=str,
    default="0.1.0",
)

# Vault configuration
REQUIRE_VAULT_FOR_EVALUATION = get_env_variable(
    name="REQUIRE_VAULT_FOR_EVALUATION",
    type_=str,
    default="false",
).lower() == "true"

MINIMUM_VAULT_BALANCE_USD = get_env_variable(
    name="MINIMUM_VAULT_BALANCE_USD",
    type_=float,
    default=1000.0,
)

