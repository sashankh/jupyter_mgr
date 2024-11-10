from flask import Flask, request, jsonify
from functools import wraps
import docker
import random
import string
import os
import socket
import logging
from pathlib import Path
import sys
import signal

app = Flask(__name__)

# =======================
# Configuration Settings
# =======================

# Docker image for Jupyter Notebooks
JUPYTER_IMAGE = os.getenv('JUPYTER_IMAGE', 'jupyter/base-notebook:latest')

# Port range for mapping Jupyter Notebook containers
JUPYTER_PORT_START = int(os.getenv('JUPYTER_PORT_START', 9000))
JUPYTER_PORT_END = int(os.getenv('JUPYTER_PORT_END', 9999))

# Host IP address
HOST_IP = os.getenv('HOST_IP', '0.0.0.0')

# API key for authenticating requests
API_KEY = os.getenv('API_KEY', 'your_secure_api_key')  # Replace with your actual API key

# Directory to store notebooks on the host
NOTEBOOKS_DIR = Path(os.getenv('NOTEBOOKS_DIR', 'notebooks'))

# Resource limits for Docker containers
MAX_MEMORY = os.getenv('MAX_MEMORY', '2g')       # Example: '2g' for 2 Gigabytes
CPU_QUOTA = int(os.getenv('CPU_QUOTA', 50000))   # Example: 50000 for 50% of a CPU core

# =====================
# Logger Initialization
# =====================

# Configure logging to output informational messages and above
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =====================
# Docker Client Setup
# =====================

try:
    # Initialize Docker client from environment
    client = docker.from_env()
    client.ping()
    logger.info("Docker client initialized successfully.")
except docker.errors.DockerException as e:
    logger.error(f"Failed to initialize Docker client: {e}")
    sys.exit(1)  # Exit application if Docker is not available

# =====================
# Utility Functions
# =====================

def generate_random_string(length=8):
    """
    Generate a random string of fixed length.
    
    Args:
        length (int): Length of the generated string.
        
    Returns:
        str: Randomly generated string.
    """
    characters = string.ascii_lowercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def find_available_port(retries=5):
    """
    Find an available port within the specified range with a limited number of retries.
    
    Args:
        retries (int): Number of attempts to find a free port.
        
    Returns:
        int: Available port number.
        
    Raises:
        RuntimeError: If no available port is found after the specified retries.
    """
    for _ in range(retries):
        port = random.randint(JUPYTER_PORT_START, JUPYTER_PORT_END)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST_IP, port))
                return port
            except OSError:
                continue
    raise RuntimeError("No available ports found within the specified range after multiple attempts.")

def require_api_key(f):
    """
    Decorator to enforce API key authentication.
    
    Args:
        f (function): The route handler function to decorate.
        
    Returns:
        function: The decorated function.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get('x-api-key')
        if key and key == API_KEY:
            return f(*args, **kwargs)
        else:
            logger.warning("Unauthorized access attempt.")
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    return decorated

# =====================
# Flask Routes
# =====================

@app.route('/create_notebook', methods=['POST'])
@require_api_key
def create_notebook():
    """
    Endpoint to create a new Jupyter Notebook instance.
    
    Returns:
        JSON response containing notebook URL, container name, and port.
    """
    try:
        # Generate unique identifiers
        unique_id = generate_random_string()
        port = find_available_port()
        container_name = f'jupyter-{unique_id}'

        # Run the Jupyter Notebook Docker container
        container = client.containers.run(
            JUPYTER_IMAGE,
            name=container_name,
            ports={'8888/tcp': port},
            detach=True,
            tty=True,
            volumes={
                str(NOTEBOOKS_DIR.resolve()): {
                    'bind': '/home/jovyan/work',
                    'mode': 'rw'
                }
            },
            environment={
                'JUPYTER_TOKEN': '',  # To be addressed later for security
                'GRANT_SUDO': 'yes',
            },
            mem_limit=MAX_MEMORY,
            cpu_quota=CPU_QUOTA,
            labels={
                'app': 'jupyter-notebook',
                'managed_by': 'flask_app'
            }
        )

        logger.info(f"Started container '{container_name}' on port {port}.")

        # Construct the notebook URL
        notebook_url = f'http://{HOST_IP}:{port}/'

        return jsonify({
            'success': True,
            'notebook_url': notebook_url,
            'container_name': container_name,
            'port': port
        }), 201

    except docker.errors.APIError as e:
        logger.error(f"Docker API error: {e}")
        return jsonify({'success': False, 'error': 'Docker API error.'}), 500
    except RuntimeError as e:
        logger.error(e)
        return jsonify({'success': False, 'error': str(e)}), 500
    except Exception as e:
        logger.exception("An unexpected error occurred.")
        return jsonify({'success': False, 'error': 'An unexpected error occurred.'}), 500

@app.route('/list_notebooks', methods=['GET'])
@require_api_key
def list_notebooks():
    """
    Endpoint to list all active Jupyter Notebook instances.
    
    Returns:
        JSON response containing a list of notebooks with their details.
    """
    try:
        # Filter containers by labels for precise selection
        containers = client.containers.list(all=True, filters={
            'label': 'app=jupyter-notebook',
            'label': 'managed_by=flask_app'
        })
        notebook_list = []
        for container in containers:
            ports = container.attrs['NetworkSettings']['Ports'].get('8888/tcp')
            host_port = ports[0]['HostPort'] if ports else None
            notebook_list.append({
                'name': container.name,
                'status': container.status,
                'port': host_port,
                'ip': container.attrs['NetworkSettings']['IPAddress']
            })
        return jsonify({'success': True, 'notebooks': notebook_list}), 200
    except docker.errors.APIError as e:
        logger.error(f"Docker API error: {e}")
        return jsonify({'success': False, 'error': 'Docker API error.'}), 500
    except Exception as e:
        logger.exception("An unexpected error occurred.")
        return jsonify({'success': False, 'error': 'An unexpected error occurred.'}), 500

@app.route('/delete_notebook/<container_name>', methods=['DELETE'])
@require_api_key
def delete_notebook(container_name):
    """
    Endpoint to delete a specific Jupyter Notebook instance.
    
    Args:
        container_name (str): The name of the Docker container to delete.
        
    Returns:
        JSON response indicating success or failure.
    """
    try:
        container = client.containers.get(container_name)
        container.stop()
        container.remove()
        logger.info(f"Stopped and removed container '{container_name}'.")
        return jsonify({'success': True, 'message': f'Container {container_name} stopped and removed.'}), 200
    except docker.errors.NotFound:
        logger.warning(f"Container '{container_name}' not found.")
        return jsonify({'success': False, 'error': 'Container not found.'}), 404
    except docker.errors.APIError as e:
        logger.error(f"Docker API error: {e}")
        return jsonify({'success': False, 'error': 'Docker API error.'}), 500
    except Exception as e:
        logger.exception("An unexpected error occurred.")
        return jsonify({'success': False, 'error': 'An unexpected error occurred.'}), 500

# =====================
# Initialization Function
# =====================

def init():
    """
    Initialize the application environment by ensuring necessary directories exist
    and pulling the required Docker image.
    """
    try:
        # Ensure notebooks directory exists
        NOTEBOOKS_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"Ensured notebooks directory exists at {NOTEBOOKS_DIR.resolve()}.")

        # Pull the latest Jupyter Docker image
        logger.info(f"Pulling Docker image '{JUPYTER_IMAGE}'...")
        client.images.pull(JUPYTER_IMAGE)
        logger.info(f"Docker image '{JUPYTER_IMAGE}' is ready.")
    except docker.errors.APIError as e:
        logger.error(f"Failed to pull Docker image '{JUPYTER_IMAGE}': {e}")
        raise
    except Exception as e:
        logger.exception("Failed to initialize the application environment.")
        raise

# =====================
# Graceful Shutdown Handling
# =====================

def shutdown_handler(signum, frame):
    """
    Handle shutdown signals to gracefully stop and remove all Jupyter Notebook containers.
    
    Args:
        signum (int): Signal number.
        frame: Current stack frame.
    """
    logger.info("Shutting down Flask application. Stopping all Jupyter containers.")
    try:
        # Retrieve all containers managed by this application
        containers = client.containers.list(all=True, filters={
            'label': 'app=jupyter-notebook',
            'label': 'managed_by=flask_app'
        })
        for container in containers:
            try:
                container.stop()
                container.remove()
                logger.info(f"Stopped and removed container '{container.name}'.")
            except docker.errors.APIError as e:
                logger.error(f"Error stopping/removing container '{container.name}': {e}")
            except Exception as e:
                logger.error(f"Unexpected error stopping/removing container '{container.name}': {e}")
    except Exception as e:
        logger.error(f"Error during shutdown cleanup: {e}")
    finally:
        sys.exit(0)

# Register signal handlers for graceful shutdown
signal.signal(signal.SIGINT, shutdown_handler)   # Handle Ctrl+C
signal.signal(signal.SIGTERM, shutdown_handler)  # Handle termination signals

# =====================
# Application Entry Point
# =====================

if __name__ == '__main__':
    try:
        init()
        app.run(host=HOST_IP, port=8000, debug=False)
    except Exception as e:
        logger.error(f"Application failed to start: {e}")
        sys.exit(1)
