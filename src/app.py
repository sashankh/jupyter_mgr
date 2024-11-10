from flask import Flask, request, jsonify
from functools import wraps
import docker
import random
import string
import os

app = Flask(__name__)

# Configuration
JUPYTER_IMAGE = 'jupyter/base-notebook:latest'  # Use a custom image if necessary
JUPYTER_PORT_START = 9000  # Starting port for Jupyter Notebooks
JUPYTER_PORT_END = 9999    # Ending port for Jupyter Notebooks
HOST_IP = '0.0.0.0'         # Host IP to bind
API_KEY = 'your_secure_api_key'  # Replace with a strong, unique key

# Initialize Docker client
client = docker.from_env()

# Utility function to generate a random string
def generate_random_string(length=8):
    letters = string.ascii_lowercase + string.digits
    return ''.join(random.choice(letters) for i in range(length))

# Utility function to find a free port
def find_free_port():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

# Decorator for API key authentication
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get('x-api-key')
        if key and key == API_KEY:
            return f(*args, **kwargs)
        else:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    return decorated

@app.route('/create_notebook', methods=['POST'])
@require_api_key
def create_notebook():
    try:
        unique_id = generate_random_string()
        port = find_free_port()
        container_name = f'jupyter-{unique_id}'

        container = client.containers.run(
            JUPYTER_IMAGE,
            name=container_name,
            ports={'8888/tcp': port},
            detach=True,
            tty=True,
            volumes={
                os.path.abspath('notebooks'): {
                    'bind': '/home/jovyan/work',
                    'mode': 'rw'
                }
            },
            environment={
                'JUPYTER_TOKEN': '',  # Disable token if using password
                'GRANT_SUDO': 'yes',  # Optional: allow sudo in notebooks
            },
            mem_limit='2g',    # Limit memory to 2GB
            cpu_period=100000, # CPU period
            cpu_quota=50000    # Limit to 50% of a CPU core
        )

        # Optionally, wait for the container to initialize
        container.reload()

        # Construct the notebook URL
        notebook_url = f'http://{request.host.split(":")[0]}:{port}/'

        return jsonify({
            'success': True,
            'notebook_url': notebook_url,
            'container_name': container_name,
            'port': port
        }), 201

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/list_notebooks', methods=['GET'])
@require_api_key
def list_notebooks():
    try:
        containers = client.containers.list(filters={'name': 'jupyter-'})
        notebook_list = []
        for container in containers:
            ports = container.attrs['NetworkSettings']['Ports']['8888/tcp']
            host_port = ports[0]['HostPort'] if ports else None
            notebook_list.append({
                'name': container.name,
                'status': container.status,
                'port': host_port,
                'ip': container.attrs['NetworkSettings']['IPAddress']
            })
        return jsonify({'success': True, 'notebooks': notebook_list}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/delete_notebook/<container_name>', methods=['DELETE'])
@require_api_key
def delete_notebook(container_name):
    try:
        container = client.containers.get(container_name)
        container.stop()
        container.remove()
        return jsonify({'success': True, 'message': f'Container {container_name} stopped and removed.'}), 200
    except docker.errors.NotFound:
        return jsonify({'success': False, 'error': 'Container not found.'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host=HOST_IP, port=8000, debug=False)
