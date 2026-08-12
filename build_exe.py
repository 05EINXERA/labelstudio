import PyInstaller.__main__
import sys

def build():
    print("Starting PyInstaller build...")
    
    # We want to use the venv's python paths to avoid missing modules
    
    args = [
        'run_exe.py',
        '--name=LabelStudioApp',
        '--onedir',                # Build as a folder, not a single huge file
        '--noconfirm',             # Overwrite existing builds
        '--clean',
        
        # Include static frontend and database migrations
        '--add-data=frontend;frontend',
        '--add-data=alembic;alembic',
        '--add-data=alembic.ini;.',
        '--add-data=.env;.',
        
        # Hidden imports that might not be picked up automatically
        '--hidden-import=passlib.handlers.bcrypt',
        '--hidden-import=psycopg',
        '--hidden-import=python-multipart',
        
        # We assume Ultralytics and torch are picked up, but just in case:
        '--hidden-import=ultralytics',
        '--hidden-import=torch',
        '--hidden-import=torchvision',
        '--hidden-import=transformers',
        
        # Avoid bundling heavy model weights if they exist in models/
        # PyInstaller doesn't bundle extra directories unless told to via --add-data, 
        # so models/ and uploads/ are naturally excluded.
    ]
    
    PyInstaller.__main__.run(args)
    print("Build complete! Check the 'dist' directory.")

if __name__ == '__main__':
    build()
