# Reverse Proxy & TLS Termination Guide (LAN Deployment)

This guide explains how to set up TLS termination in front of the Annotation Workspace single-worker backend process using **Caddy** (recommended for Windows LAN) or **Nginx**.

---

## 1. Why Use a Reverse Proxy & TLS?

1. **Traffic Encryption (`HTTPS`)**: Encrypts annotator login credentials, session tokens, image data, and annotations across the local office network.
2. **Secure Session Cookies (`COOKIE_SECURE=1`)**: Prevents browsers from transmitting auth cookies (`access_token`, `csrf_token`) over unencrypted plaintext HTTP.
3. **Single-Worker Backend Protection**: Terminates SSL and handles static compression off the Python event loop while preserving Uvicorn's single-worker ML memory footprint.
4. **Large File Streaming**: Manages buffered client uploads (up to 300MB dataset ZIP files) and unbuffered real-time inference streams.

---

## 2. Option A: Caddy (Recommended for Windows)

Caddy provides automated local certificates without requiring OpenSSL configuration.

### Quick Setup

Run the automated PowerShell installer as Administrator:

```powershell
.\scripts\setup-caddy.ps1 -StartImmediately
```

This script will:
1. Download `caddy.exe` into `scripts\caddy\` (if not already on `PATH`).
2. Validate [Caddyfile](file:///c:/labelstudio/Caddyfile).
3. Trust Caddy's internal Certificate Authority (CA) in the Windows Certificate Store.
4. Export the local Root CA certificate to `certs/caddy-lan-root.crt`.
5. Register the Windows Scheduled Task `AnnotationProxy` to start at system boot.

### Configuring Client Workstations

To prevent HTTPS warning banners on client annotator PCs across the LAN:
1. Copy `certs/caddy-lan-root.crt` to the client computer.
2. Double-click `caddy-lan-root.crt` -> **Install Certificate...** -> Select **Local Machine** -> Place in **Trusted Root Certification Authorities**.

---

## 3. Option B: Nginx

For Linux servers or Windows servers running an existing Nginx instance:

1. Copy [deploy/nginx.conf](file:///c:/labelstudio/deploy/nginx.conf) to your Nginx configuration directory:
   - Linux: `/etc/nginx/sites-available/annotation-workspace`
   - Windows: `C:\nginx\conf\nginx.conf`
2. Install your SSL certificate and private key at:
   - `/etc/ssl/certs/annotation_app.crt`
   - `/etc/ssl/private/annotation_app.key`
3. Reload or restart Nginx:
   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```

---

## 4. Enabling Secure Cookies in `.env`

Once TLS is terminating in front of the backend, enable secure cookies and trusted proxy headers in `.env`:

```ini
# Enforce secure cookies (transmitted only over HTTPS)
COOKIE_SECURE=1
COOKIE_SAMESITE=strict

# Trust reverse proxy X-Forwarded-* headers from loopback
FORWARDED_ALLOW_IPS=127.0.0.1
PROXY_HEADERS=1

# CORS origin should use https
CORS_ORIGINS=https://192.168.1.81,https://annotation.local
```

Restart the backend service:
```powershell
Restart-ScheduledTask -TaskName "AnnotationApp"
```

---

## 5. Verification & Health Check

Test the HTTPS connection and verify response headers:

```powershell
# Health check via proxy
curl.exe -k -i https://localhost/health

# Verify X-Content-Type-Options and Strict-Transport-Security headers
curl.exe -k -I https://localhost/
```
