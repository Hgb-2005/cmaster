# JSON account/progress storage uses in-process locks: exactly one worker.
bind = "0.0.0.0:8080"
workers = 1
worker_class = "gthread"
threads = 8
timeout = 130
graceful_timeout = 75
accesslog = "-"
errorlog = "-"
