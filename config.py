import os

# DATA_DIR allows us to store persistent data on a mounted disk (like Render's Persistent Disk at /data)
DATA_DIR = os.environ.get("DATA_DIR", ".")

# Ensure the DATA_DIR exists if it's customized
if DATA_DIR != "." and not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)
