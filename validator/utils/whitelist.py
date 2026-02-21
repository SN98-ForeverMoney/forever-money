import json
import logging
import os
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

WHITELIST_FILE = "miner_whitelist.json"

def get_whitelisted_miners() -> List[Dict[str, str]]:
    """
    Read the whitelist file and return list of whitelisted miner entries.
    
    Returns:
        List of dicts with 'name' and 'hotkey' keys.
        Returns empty list if file doesn't exist or is invalid.
    """
    if not os.path.exists(WHITELIST_FILE):
        return []
        
    try:
        with open(WHITELIST_FILE, "r") as f:
            whitelist = json.load(f)
            if not isinstance(whitelist, list):
                logger.warning(f"Whitelist file {WHITELIST_FILE} must contain a list")
                return []
            return whitelist
    except json.JSONDecodeError:
        logger.error(f"Failed to parse whitelist file {WHITELIST_FILE}")
        return []
    except Exception as e:
        logger.error(f"Error reading whitelist file {WHITELIST_FILE}: {e}")
        return []

def is_miner_whitelisted(hotkey: str) -> bool:
    """
    Check if a miner hotkey is in the whitelist.
    
    Args:
        hotkey: The miner's hotkey address
        
    Returns:
        True if whitelisted, False otherwise
    """
    if not hotkey:
        return False
        
    whitelist = get_whitelisted_miners()
    for item in whitelist:
        if item.get("hotkey") == hotkey:
            return True
            
    return False
