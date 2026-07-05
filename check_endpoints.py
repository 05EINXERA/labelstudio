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

if __name__ == '__main__':
    req('GET', 'http://127.0.0.1:8765/')
    req('POST', 'http://127.0.0.1:8765/api/detect', b'{"image":""}')
    req('POST', 'http://127.0.0.1:8765/api/label-studio/send', b'{"foo":"bar"}')
