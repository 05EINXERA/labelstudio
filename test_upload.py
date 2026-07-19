import requests

files = [
    ("file", ("test1.jpg", b"fake image data 1", "image/jpeg")),
    ("file", ("test2.jpg", b"fake image data 2", "image/jpeg")),
]

res = requests.post(
    "http://127.0.0.1:8765/api/projects/1/upload?assignee=Guest", files=files
)
print("Status Code:", res.status_code)
print("Response:", res.text)
