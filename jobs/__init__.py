"""Heavy jobs (exports, annotation imports) run outside the web process.

`runner.py` lives in the web process and schedules, supervises and reports on
jobs; `worker.py` is what a job actually runs, in a child process. See
.devnotes/fix-exports-imports/.
"""
