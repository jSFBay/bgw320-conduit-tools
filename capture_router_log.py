#!/usr/bin/env python3
"""Poll a BGW320's own event log (cgi-bin/logs.ha) and append new entries
to a persistent local file, since the router's own buffer is short-lived
(observed as few as ~9 minutes / ~250 entries before eviction, faster
under heavier traffic) and offers no push/webhook — polling is the only
option. Run frequently (every ~5 min via cron/LaunchAgent/systemd timer)
so consecutive polls overlap and nothing gets silently evicted between
checks.

Dedup/gap-detection is timestamp-based, not based on the page's "No."
column: that column stayed stable for the same entry across two polls
8 seconds apart while the buffer was still filling, but it's unconfirmed
whether it renumbers by display position once eviction starts.
Timestamps are absolute and can't renumber, so they're the safe source of
truth for both dedup and detecting a real gap (entries evicted before any
poll captured them).

Requires bgw320_ippass.py (in the same directory) for login/config —
see that script's docstring for the BGW320_* environment variables.

Configuration (in addition to bgw320_ippass.py's variables):
  ROUTER_LOG_DATA_FILE   default: ~/router-log-data.log
  ROUTER_LOG_STATE_FILE  default: ~/.router-log-state
"""

import sys
import os
import re
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bgw320_ippass import curl, login, PASS_FILE, ROUTER

DATA_FILE = os.environ.get('ROUTER_LOG_DATA_FILE', os.path.expanduser('~/router-log-data.log'))
STATE_FILE = os.environ.get('ROUTER_LOG_STATE_FILE', os.path.expanduser('~/.router-log-state'))

ROW_RE = re.compile(
    r'(\d+) \| \| (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+) \| \| '
    r'([0-9a-fA-F.:]+) \| \| ([0-9a-fA-F.:]+) \| \| '
    r'(TCP|UDP|ICMPv6|ICMP) \| \| ([A-Za-z ()., ]+?) \| \|'
)


def log(msg):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'{ts} {msg}')


def fetch_rows():
    with open(PASS_FILE) as f:
        login(f.read().strip())
    html = curl([f'{ROUTER}/cgi-bin/logs.ha'])
    text = re.sub(r'<[^>]+>', ' | ', html)
    text = re.sub(r'\s+', ' ', text)
    rows = ROW_RE.findall(text)
    # rows: list of (no, timestamp, src, dst, proto, reason), oldest first
    return rows


def main():
    try:
        with open(STATE_FILE) as f:
            last_ts = f.read().strip()
    except FileNotFoundError:
        last_ts = None

    rows = fetch_rows()
    if not rows:
        log('WARNING: fetched 0 rows from router log (login failed or page format changed?)')
        sys.exit(1)

    oldest_ts_this_poll = rows[0][1]
    newest_ts_this_poll = rows[-1][1]

    if last_ts is not None and oldest_ts_this_poll > last_ts:
        gap_start = datetime.datetime.fromisoformat(last_ts)
        gap_end = datetime.datetime.fromisoformat(oldest_ts_this_poll)
        gap_seconds = (gap_end - gap_start).total_seconds()
        log(f'WARNING: gap detected — router buffer evicted entries between '
            f'{last_ts} and {oldest_ts_this_poll} (~{gap_seconds:.0f}s) before this poll could capture them')

    new_rows = [r for r in rows if last_ts is None or r[1] > last_ts]

    if new_rows:
        with open(DATA_FILE, 'a') as f:
            for no, ts, src, dst, proto, reason in new_rows:
                f.write(f'{ts} | {src} | {dst} | {proto} | {reason.strip()}\n')
        log(f'Captured {len(new_rows)} new entries (newest: {newest_ts_this_poll})')
    else:
        log('No new entries since last poll')

    with open(STATE_FILE, 'w') as f:
        f.write(newest_ts_this_poll)


if __name__ == '__main__':
    main()
