#!/usr/bin/env python3
"""Switch a BGW320's IP Passthrough between two MAC addresses (e.g. an
Ethernet and a Wi-Fi interface), for setups where the router needs to
follow whichever interface is currently active.

Also useful standalone: logs in and can report/set the router's IP
Passthrough target without any interface-switching logic of your own.

Configuration is via environment variables (all required except
BGW320_ROUTER_URL and BGW320_PASS_FILE, which have defaults):

  BGW320_ROUTER_URL   default: http://192.168.1.254
  BGW320_PASS_FILE    default: ~/.bgw320_pass (router admin password, one line)
  BGW320_ETHERNET_MAC (required for the ethernet/wifi subcommands)
  BGW320_WIFI_MAC     (required for the ethernet/wifi subcommands)

Usage:
  bgw320_ippass.py status              # report current IP Passthrough target
  bgw320_ippass.py ethernet            # bind IP Passthrough to BGW320_ETHERNET_MAC
  bgw320_ippass.py wifi                # bind IP Passthrough to BGW320_WIFI_MAC
"""

import sys
import os
import re
import hashlib
import subprocess
import time
import datetime

ROUTER = os.environ.get('BGW320_ROUTER_URL', 'http://192.168.1.254')
PASS_FILE = os.environ.get('BGW320_PASS_FILE', os.path.expanduser('~/.bgw320_pass'))
ETHERNET_MAC = os.environ.get('BGW320_ETHERNET_MAC')
WIFI_MAC = os.environ.get('BGW320_WIFI_MAC')
COOKIE_JAR = '/tmp/bgw320_session.txt'


def log(msg):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'{ts} {msg}')


def curl(args, **kwargs):
    cmd = ['/usr/bin/curl', '-s', '--max-time', '10',
           '-c', COOKIE_JAR, '-b', COOKIE_JAR] + args
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout


def login(password):
    curl([f'{ROUTER}/'])  # initialize session cookie
    html = curl([f'{ROUTER}/cgi-bin/login.ha'])
    m = re.search(r'nonce[^>]+value="([^"]+)"', html)
    if not m:
        raise RuntimeError('Could not find nonce on login page')
    nonce = m.group(1)
    hash_pwd = hashlib.md5((password + nonce).encode()).hexdigest()
    curl(['-X', 'POST',
          '--data-urlencode', f'nonce={nonce}',
          '--data-urlencode', 'password=*',
          '--data-urlencode', f'hashpassword={hash_pwd}',
          '--data-urlencode', 'Continue=Continue',
          f'{ROUTER}/cgi-bin/login.ha'])


def get_ippass_nonce():
    html = curl([f'{ROUTER}/cgi-bin/ippass.ha'])
    m = re.search(r'nonce[^>]+value="([^"]+)"', html)
    if not m:
        raise RuntimeError('Could not get IP Passthrough page (login failed?)')
    return m.group(1)


def get_current_mac():
    html = curl([f'{ROUTER}/cgi-bin/ippass.ha'])
    m = re.search(r'name="passmac" value="([^"]+)"', html)
    if not m:
        raise RuntimeError('Could not read current passmac (login failed?)')
    return m.group(1)


def set_mac(mac):
    nonce = get_ippass_nonce()
    import urllib.parse
    body = urllib.parse.urlencode({
        'nonce': nonce, 'allocmode': 'passthrough', 'passmode': 'dhcps-fixed',
        'ippassmaclist': '',  # empty = manual entry; sending MAC here conflicts with passmac
        'passmac': mac,
        'dhcpday': '7', 'dhcphour': '0', 'dhcpmin': '0', 'dhcpsec': '0',
        'Save': 'Save',
    })
    curl(['-X', 'POST',
          '-H', 'Content-Type: application/x-www-form-urlencoded',
          '--data', body,
          f'{ROUTER}/cgi-bin/ippass.ha'])
    # Verify by re-reading the page
    time.sleep(2)
    html = curl([f'{ROUTER}/cgi-bin/ippass.ha'])
    m = re.search(r'name="passmac" value="([^"]+)"', html)
    return m.group(1) if m else 'unknown'


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in ('ethernet', 'wifi', 'status'):
        log('Usage: bgw320_ippass.py ethernet|wifi|status')
        sys.exit(1)

    target = sys.argv[1]

    try:
        with open(PASS_FILE) as f:
            password = f.read().strip()

        log('Logging in to BGW320...')
        login(password)

        if target == 'status':
            current = get_current_mac()
            label = {ETHERNET_MAC: 'ethernet', WIFI_MAC: 'wifi'}.get(current, 'unknown')
            log(f'CURRENT_MAC={current} ({label})')
            sys.exit(0)

        if not ETHERNET_MAC or not WIFI_MAC:
            log('ERROR — BGW320_ETHERNET_MAC and BGW320_WIFI_MAC must both be set to use ethernet/wifi subcommands')
            sys.exit(1)

        mac = ETHERNET_MAC if target == 'ethernet' else WIFI_MAC

        log(f'Setting IP Passthrough to {target} ({mac})...')
        confirmed = set_mac(mac)

        if confirmed == mac:
            log(f'OK — IP Passthrough now bound to {mac} ({target})')
            sys.exit(0)
        else:
            log(f'ERROR — expected {mac} but router shows {confirmed}')
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        log(f'ERROR — unhandled exception: {type(e).__name__}: {e}')
        sys.exit(1)
