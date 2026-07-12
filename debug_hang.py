import sys, threading, traceback, time
def dump_threads():
    for th in threading.enumerate():
        print(th)
        traceback.print_stack(sys._current_frames()[th.ident])
        print()
import main
def worker():
    import urllib.request
    try: urllib.request.urlopen("http://127.0.0.1:8002/api/data", timeout=3)
    except Exception as e: print("Worker err:", e)
import uvicorn
def run_server():
    uvicorn.run("main:app", host="127.0.0.1", port=8002)
threading.Thread(target=run_server, daemon=True).start()
time.sleep(3)
print("Sending request...")
threading.Thread(target=worker).start()
time.sleep(5)
dump_threads()
