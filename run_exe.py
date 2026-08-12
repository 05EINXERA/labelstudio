import multiprocessing
import uvicorn
import os
import sys

# When frozen by PyInstaller, sys._MEIPASS holds the path to the extracted files
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # Change the current working directory to the extracted PyInstaller folder
    # so that relative paths like "frontend/" and ".env" resolve correctly.
    os.chdir(sys._MEIPASS)

if __name__ == '__main__':
    # Required for Windows executables using multiprocessing
    multiprocessing.freeze_support()
    
    # Import the FastAPI app instance directly to avoid string-import issues in frozen binaries
    import main
    from config import APP_HOST, APP_PORT
    
    # Run uvicorn. Note: reload=True and workers>1 are not supported in this frozen configuration 
    # without a string import, but we need workers=1 anyway for this app.
    print(f"Starting server on {APP_HOST}:{APP_PORT}...")
    uvicorn.run(main.app, host=APP_HOST, port=int(APP_PORT), proxy_headers=True, forwarded_allow_ips="*")
