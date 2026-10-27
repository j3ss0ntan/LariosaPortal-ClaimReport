# Deploying to a VPC server

Production stack: **gunicorn** (WSGI server, bound to `127.0.0.1:8501` only)
behind **nginx** (public-facing reverse proxy on port 80/443), run as a
**systemd** service so it restarts on crash and on boot.

Assumes a Debian/Ubuntu VPC with `sudo` access. Adjust paths/package manager
for other distros.

## 1. Install system packages

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip nginx git
```

## 2. Create a dedicated, unprivileged service user

```bash
sudo useradd --system --create-home --home-dir /opt/eclaims-report --shell /usr/sbin/nologin eclaims
```

## 3. Get the code onto the box

```bash
sudo -u eclaims git clone <your-repo-url> /opt/eclaims-report
cd /opt/eclaims-report
```

(No repo host yet? `scp -r` the project directory to `/opt/eclaims-report`
instead, then `sudo chown -R eclaims:eclaims /opt/eclaims-report`.)

## 4. Python environment

```bash
sudo -u eclaims python3 -m venv /opt/eclaims-report/.venv
sudo -u eclaims /opt/eclaims-report/.venv/bin/pip install -r /opt/eclaims-report/requirements.txt
```

## 5. Initialize the database

```bash
cd /opt/eclaims-report
sudo -u eclaims .venv/bin/python migrate.py
```

This creates `eurolink.db` (users, audit log, claims source, password
policy) and seeds the default admin (`jessontan` / `ChangeMe123!` — you'll
be forced to change it on first login). `.flask_secret_key` is generated
automatically on first run of the app itself if it doesn't already exist.

Both files are gitignored and must stay writable by the `eclaims` user —
they're runtime state, not code, and back them up (`eurolink.db` holds every
account and audit record).

## 6. systemd service

```bash
sudo cp deploy/eclaims.service /etc/systemd/system/eclaims.service
sudo systemctl daemon-reload
sudo systemctl enable --now eclaims
sudo systemctl status eclaims
```

Logs:

```bash
sudo journalctl -u eclaims -f
```

## 7. nginx reverse proxy

```bash
sudo cp deploy/nginx-eclaims.conf /etc/nginx/sites-available/eclaims-report
sudo sed -i 's/your-domain-or-ip/<your domain or server IP>/' /etc/nginx/sites-available/eclaims-report
sudo ln -s /etc/nginx/sites-available/eclaims-report /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

The app is now reachable at `http://<your domain or IP>/lariosa/eclaims-report/`
(the bare `/` intentionally returns a blank page — see `app.py`'s
`_root_blank`).

## 8. Firewall

Only 80/443 (and 22 for SSH) need to be open to the internet — gunicorn
listens on `127.0.0.1:8501` only, never directly exposed.

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

## 9. HTTPS (recommended before real use)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d <your-domain>
```

(Requires a real domain pointed at the server; a bare IP can't get a
browser-trusted cert.)

## Redeploying after a code change

```bash
cd /opt/eclaims-report
sudo -u eclaims git pull
sudo -u eclaims .venv/bin/pip install -r requirements.txt
sudo -u eclaims .venv/bin/python migrate.py   # safe/idempotent even with no schema change
sudo systemctl restart eclaims
```

## Notes

- `GUNICORN_WORKERS` (default 3) and `GUNICORN_BIND` (default
  `127.0.0.1:8501`) are read from the environment in `gunicorn.conf.py` if
  you need to override either — add them under `[Service]` in
  `eclaims.service` as `Environment="GUNICORN_WORKERS=5"`.
- `MAX_CONTENT_LENGTH` in `app.py` (currently 100 MB) and `client_max_body_size`
  in the nginx config must stay in sync — nginx's limit is checked first.
- This app has no Google OAuth / external IdP — it's self-contained
  username+password auth backed by `eurolink.db`. No extra provider
  configuration is needed for this deployment.
