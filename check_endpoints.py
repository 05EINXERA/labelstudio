import urllib.request, urllib.error

def req(method, url, data=None):
    req = urllib.request.Request(url, data=data, headers={'Content-Type':'application/json'} if data else {})
    req.method = method
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print('---', method, url, '---')
            print(resp.status)
            body = resp.read().decode('utf-8', errors='replace')
            print(body[:1000])
    except urllib.error.HTTPError as e:
        print('---', method, url, 'HTTP', e.code, '---')
        try:
            body = e.read().decode('utf-8', errors='replace')
            print(body[:1000])
        except Exception as ex:
            print('No body,', ex)
    except Exception as e:
        print('---', method, url, 'ERROR ---')
        print(e)

import time
import json

def req_poll(method, url, data=None):
    job_id = None
    try:
        req_obj = urllib.request.Request(url, data=data, headers={'Content-Type':'application/json'} if data else {})
        req_obj.method = method
        with urllib.request.urlopen(req_obj, timeout=10) as resp:
            body = resp.read().decode('utf-8')
            print('---', method, url, '---', resp.status)
            payload = json.loads(body)
            job_id = payload.get("job_id")
    except Exception as e:
        print('ERROR', e)
        return
        
    if not job_id:
        return
        
    for _ in range(10):
        try:
            status_req = urllib.request.Request(f'http://127.0.0.1:8765/api/detect/status/{job_id}')
            with urllib.request.urlopen(status_req, timeout=10) as resp:
                body = resp.read().decode('utf-8')
                status_payload = json.loads(body)
                if status_payload.get("status") != "pending":
                    print("Job finished:", str(status_payload)[:500])
                    break
        except Exception as e:
            print("Status fetch error", e)
        time.sleep(1)

if __name__ == '__main__':
    req('GET', 'http://127.0.0.1:8765/')
    req_poll('POST', 'http://127.0.0.1:8765/api/detect', b'{"image":""}')
    req('POST', 'http://127.0.0.1:8765/api/label-studio/send', b'{"foo":"bar"}')
