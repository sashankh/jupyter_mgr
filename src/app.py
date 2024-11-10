import docker
import uuid
import os
import socket
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from threading import Lock

app = FastAPI()

# Initialize templates
templates = Jinja2Templates(directory="templates")

# Initialize Docker client
client = docker.from_env()

# Configuration
JUPYTER_IMAGE = 'jupyter/datascience-notebook:latest'
HOST_PORT_START = 8801  # Starting port number
HOST_PORT_END = 8990    # Ending port number
NOTES_DIR = os.path.abspath('./notebooks')
CONFIGS_DIR = os.path.abspath('./configs')  # Directory for config files
FRONTEND_DOMAIN = 'http://localhost:5000'  # Replace with your frontend's domain

# Ensure necessary directories exist
os.makedirs(NOTES_DIR, exist_ok=True)
os.makedirs(CONFIGS_DIR, exist_ok=True)

# In-memory store for container info
# Structure: {container_id: { 'name': ..., 'url': ..., 'port': ..., 'token': ..., 'config_path': ... }}
containers_info = {}
containers_lock = Lock()  # To handle concurrent access

def get_available_port():
    """Find an available port between HOST_PORT_START and HOST_PORT_END."""
    for port in range(HOST_PORT_START, HOST_PORT_END):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('localhost', port))
                return port
            except socket.error:
                continue
    raise Exception("No available ports")

def generate_jupyter_config(config_path, frontend_domain):
    """Generate a JupyterLab config file to allow embedding in iframes."""
    config_content = f"""
c = get_config()

# Allow specific origin to embed JupyterLab in an iframe
c.NotebookApp.allow_origin = '{frontend_domain}'
c.NotebookApp.allow_credentials = True

# Update Tornado settings to adjust headers
c.NotebookApp.tornado_settings = {{
    'headers': {{
        'Content-Security-Policy': "frame-ancestors 'self' {frontend_domain}",
    }}
}}

# Optional: Disable some security features if necessary (use with caution)
# c.NotebookApp.disable_check_xsrf = True
"""
    with open(config_path, 'w') as config_file:
        config_file.write(config_content)

def create_jupyter_container():
    """Create and start a new JupyterLab Docker container with dynamic config."""
    token = uuid.uuid4().hex  # Generate a unique token
    port = get_available_port()

    container_name = f'jupyterlab_{uuid.uuid4().hex[:8]}'

    # Generate unique config file path
    config_filename = f'jupyter_lab_config_{uuid.uuid4().hex[:8]}.py'
    config_path = os.path.join(CONFIGS_DIR, config_filename)
    generate_jupyter_config(config_path, FRONTEND_DOMAIN)

    try:
        container = client.containers.run(
            JUPYTER_IMAGE,
            detach=True,
            environment={
                'JUPYTER_ENABLE_LAB': 'yes',
                'JUPYTER_TOKEN': token
            },
            volumes={
                NOTES_DIR: {'bind': '/home/jovyan/work', 'mode': 'rw'},
                config_path: {'bind': '/home/jovyan/.jupyter/jupyter_lab_config.py', 'mode': 'ro'}
            },
            ports={'8888/tcp': port},
            name=container_name,
            restart_policy={"Name": "no"}
        )
    except docker.errors.APIError as e:
        # Clean up config file if container creation fails
        if os.path.exists(config_path):
            os.remove(config_path)
        raise Exception(f"Docker API error: {str(e)}")

    return {
        'container_id': container.id,
        'name': container.name,
        'url': f'http://localhost:{port}/lab?token={token}',
        'port': port,
        'token': token,
        'config_path': config_path
    }

def cleanup_config(config_path):
    """Delete the Jupyter config file."""
    try:
        if os.path.exists(config_path):
            os.remove(config_path)
    except Exception as e:
        print(f"Error deleting config file {config_path}: {e}")

class DeleteNotebookRequest(BaseModel):
    container_id: str

@app.post("/create_notebook", status_code=201)
def create_notebook():
    """Endpoint to create a new JupyterLab notebook."""
    try:
        with containers_lock:
            notebook_info = create_jupyter_container()
            containers_info[notebook_info['container_id']] = {
                'name': notebook_info['name'],
                'url': notebook_info['url'],
                'port': notebook_info['port'],
                'token': notebook_info['token'],
                'config_path': notebook_info['config_path'],
                'url2': f"https://crispy-space-chainsaw-gwrg7vjq9h9w5v-{notebook_info['port']}.app.github.dev/lab?token={notebook_info['token']}",
                'url3': f"https://crispy-space-chainsaw-gwrg7vjq9h9w5v-5000.app.github.dev/view.html?url=https://crispy-space-chainsaw-gwrg7vjq9h9w5v-{notebook_info['port']}.app.github.dev/lab?token={notebook_info['token']}"
            }
        return {
            'status': 'success',
            'data': {
                'container_id': notebook_info['container_id'],
                'name': notebook_info['name'],
                'url': notebook_info['url'],
                'port': notebook_info['port'],
                'token': notebook_info['token'],
                'url2': f"https://crispy-space-chainsaw-gwrg7vjq9h9w5v-{notebook_info['port']}.app.github.dev/lab?token={notebook_info['token']}",
                'url3': f"https://crispy-space-chainsaw-gwrg7vjq9h9w5v-5000.app.github.dev/view.html?url=https://crispy-space-chainsaw-gwrg7vjq9h9w5v-{notebook_info['port']}.app.github.dev/lab?token={notebook_info['token']}"
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/query_notebooks")
def query_notebooks():
    """Endpoint to retrieve all active notebooks."""
    try:
        with containers_lock:
            # Optionally, verify if containers are still running
            active_containers = []
            to_remove = []
            for cid, info in containers_info.items():
                try:
                    container = client.containers.get(cid)
                    if container.status != 'running':
                        to_remove.append(cid)
                    else:
                        active_containers.append({
                            'container_id': cid,
                            'name': info['name'],
                            'url': info['url'],
                            'port': info['port'],
                            'token': info['token']
                        })
                except docker.errors.NotFound:
                    to_remove.append(cid)

            # Clean up non-running containers and their config files
            for cid in to_remove:
                config_path = containers_info[cid]['config_path']
                cleanup_config(config_path)
                del containers_info[cid]

        return {
            'status': 'success',
            'data': active_containers
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/delete_notebook")
def delete_notebook(request: DeleteNotebookRequest):
    """Endpoint to delete a specified JupyterLab notebook."""
    container_id = request.container_id

    try:
        with containers_lock:
            container_info = containers_info.get(container_id)
            if not container_info:
                raise HTTPException(status_code=404, detail=f'Container with ID {container_id} not found.')

            container = client.containers.get(container_id)
            container.stop()
            container.remove()

            # Clean up config file
            cleanup_config(container_info['config_path'])

            del containers_info[container_id]

        return {
            'status': 'success',
            'message': f'Container {container_id} deleted successfully.'
        }
    except docker.errors.NotFound:
        with containers_lock:
            container_info = containers_info.get(container_id)
            if container_info:
                cleanup_config(container_info['config_path'])
                del containers_info[container_id]
        raise HTTPException(status_code=404, detail=f'Container with ID {container_id} not found.')
    except docker.errors.APIError as e:
        raise HTTPException(status_code=500, detail=f'Docker API error: {str(e)}')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/view_notebook/{container_id}")
def view_notebook(container_id: str, request: Request):
    """
    Serve an HTML page that embeds the JupyterLab instance in an iframe.
    """
    with containers_lock:
        container_info = containers_info.get(container_id)
        if not container_info:
            raise HTTPException(status_code=404, detail=f'Container with ID {container_id} not found.')
        jupyter_url = container_info['url']

    return templates.TemplateResponse("view_notebook.html", {"request": request, "jupyter_url": jupyter_url})

@app.get("/")
def index():
    """Simple index route."""
    return {
        'message': 'Jupyter Notebook Manager API',
        'endpoints': {
            'POST /create_notebook': 'Create a new JupyterLab notebook',
            'GET /query_notebooks': 'List all active notebooks',
            'DELETE /delete_notebook': 'Delete a specific notebook',
            'GET /view_notebook/{container_id}': 'View a notebook in an iframe'
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)