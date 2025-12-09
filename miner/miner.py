"""
Sample Miner implementation for SN98 ForeverMoney.

This is a reference implementation using a simple rule-based strategy.
Miners can replace this with ML models, optimization algorithms, or hybrid approaches.

Production Deployment:
    Use Gunicorn instead of Flask's development server:
    $ gunicorn -w 4 -b 0.0.0.0:8000 miner.miner:app

    Or with the provided script:
    $ ./run_miner.sh
"""
import os
import logging
from typing import Optional
from flask import Flask, request, jsonify
from dotenv import load_dotenv

from validator.models import (
    ValidatorRequest,
    MinerResponse,
    Strategy,
    MinerMetadata,
    RebalanceRequest,
    RebalanceResponse,
    Position
)
from validator.database import PoolDataDB
from miner.strategy import SimpleStrategyGenerator

# Load environment
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Configuration
MINER_VERSION = os.getenv('MINER_VERSION', '1.0.0-mvp')
MODEL_INFO = os.getenv('MODEL_INFO', 'simple-rule-based')
MINER_PORT = int(os.getenv('MINER_PORT', 8000))

# Initialize database connection from environment (if available)
db_connection: Optional[PoolDataDB] = None
db_connection_string = os.getenv('DB_CONNECTION_STRING')
if db_connection_string:
    try:
        db_connection = PoolDataDB(connection_string=db_connection_string)
        logger.info("Database connection initialized from environment")
    except Exception as e:
        logger.warning(f"Could not initialize database: {e}")

# Initialize strategy generator with database connection
strategy_generator = SimpleStrategyGenerator(db=db_connection)


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'version': MINER_VERSION,
        'model': MODEL_INFO,
        'db_connected': db_connection is not None
    }), 200


@app.route('/predict_strategy', methods=['POST'])
def predict_strategy():
    """
    Main endpoint for receiving strategy requests from validators.

    Expects JSON payload matching ValidatorRequest schema.
    Returns JSON matching MinerResponse schema.
    """
    try:
        # Parse request
        request_data = request.json
        if not request_data:
            return jsonify({'error': 'Invalid JSON input'}), 400

        # Validate request against schema
        try:
            validator_request = ValidatorRequest(**request_data)
        except Exception as e:
            logger.error(f"Request validation error: {e}")
            return jsonify({'error': f'Invalid request format: {str(e)}'}), 400

        logger.info(
            f"Received strategy request for pair {validator_request.pairAddress}, "
            f"block {validator_request.target_block}"
        )
        logger.info(f"Using database: {db_connection is not None}")

        # Generate strategy
        strategy = strategy_generator.generate_strategy(validator_request)

        # Construct response
        miner_response = MinerResponse(
            strategy=strategy,
            miner_metadata=MinerMetadata(
                version=MINER_VERSION,
                model_info=MODEL_INFO
            )
        )

        logger.info(
            f"Generated strategy with {len(strategy.positions)} positions"
        )

        return jsonify(miner_response.model_dump()), 200

    except Exception as e:
        logger.error(f"Error processing request: {e}", exc_info=True)
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500


@app.route('/should_rebalance', methods=['POST'])
def should_rebalance():
    """
    Endpoint for validators to ask if miner wants to rebalance at a specific block.

    This enables dynamic rebalancing based on miner's strategy logic,
    rather than simple price-based triggers.

    Expects JSON payload matching RebalanceRequest schema.
    Returns JSON matching RebalanceResponse schema.
    """
    try:
        # Parse request
        request_data = request.json
        if not request_data:
            return jsonify({'error': 'Invalid JSON input'}), 400

        # Validate request against schema
        try:
            rebalance_request = RebalanceRequest(**request_data)
        except Exception as e:
            logger.error(f"Rebalance request validation error: {e}")
            return jsonify({'error': f'Invalid request format: {str(e)}'}), 400

        logger.debug(
            f"Rebalance check for block {rebalance_request.block_number}, "
            f"price {rebalance_request.current_price:.2f}"
        )

        # Determine if we should rebalance
        # This is where miners can implement their own logic
        should_rebal, new_positions, reason = strategy_generator.should_rebalance(
            rebalance_request
        )

        # Construct response
        response = RebalanceResponse(
            rebalance=should_rebal,
            new_positions=new_positions if should_rebal else None,
            reason=reason
        )

        return jsonify(response.model_dump()), 200

    except Exception as e:
        logger.error(f"Error processing rebalance request: {e}", exc_info=True)
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500


def create_app():
    """
    Application factory for Gunicorn.

    Usage with Gunicorn:
        gunicorn -w 4 -b 0.0.0.0:8000 'miner.miner:create_app()'

    Or simply:
        gunicorn -w 4 -b 0.0.0.0:8000 miner.miner:app
    """
    return app


def main():
    """
    Run the miner server (development mode).

    For production, use Gunicorn:
        gunicorn -w 4 -b 0.0.0.0:8000 miner.miner:app
    """
    logger.info(f"Starting SN98 Miner v{MINER_VERSION}")
    logger.info(f"Model: {MODEL_INFO}")
    logger.info(f"Listening on port {MINER_PORT}")
    logger.warning("Running in development mode. For production, use Gunicorn:")
    logger.warning(f"  gunicorn -w 4 -b 0.0.0.0:{MINER_PORT} miner.miner:app")

    # Run Flask app (development only)
    app.run(
        host='0.0.0.0',
        port=MINER_PORT,
        debug=False
    )


if __name__ == '__main__':
    main()
