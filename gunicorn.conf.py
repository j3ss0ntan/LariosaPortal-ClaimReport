"""Gunicorn config for production. Run with: gunicorn -c gunicorn.conf.py app:app

Workers default low (3) rather than the usual cpu_count()*2+1: this app's
source of truth is SQLite (eurolink.db), and the only things that live in a
worker's own memory are plain recompute-from-DB caches (_cache/_upload_cache
in app.py) -- safe to duplicate across workers, just wasted CPU if you run
many of them on a small VPC box. Raise GUNICORN_WORKERS if the box has
headroom and traffic justifies it.
"""
import os

bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:8501")
workers = int(os.environ.get("GUNICORN_WORKERS", "3"))
worker_class = "sync"
timeout = 120
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = "info"
