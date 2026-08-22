# bgw320-conduit-tools

Notes and small scripts for running a WebRTC-based relay (Conduit, or
anything similar) behind an **AT&T BGW320** gateway — the router AT&T
provides with its Fiber service. If you're on this exact router and
running into connectivity weirdness, some of this may save you the
troubleshooting time it took to find in the first place.

## The main gotcha: Reflexive ACL vs. WebRTC

The BGW320's Firewall → Advanced page has a **Reflexive ACL** setting.
When it's **On**, the router denies inbound IPv6 traffic unless it's
return traffic from a connection your own network initiated outbound.
That directly conflicts with WebRTC, which needs to *receive* fresh
inbound connection attempts for ICE negotiation to complete — a relay
behind this setting will see real clients fail to connect, with no
obvious error pointing at the router as the cause.

There's no narrower middle ground here, unfortunately — we checked
whether a targeted port-based firewall pinhole could replace toggling
this setting wholesale. A live WebRTC relay had ~200 distinct UDP ports
open simultaneously, spanning nearly the entire IANA ephemeral range
(49152-65535), one port per active peer connection, and there's no flag
to restrict Conduit to a narrower range. So it's genuinely Reflexive ACL
on vs. off — no smaller pinhole is actually smaller in practice.

**Toggling it** (there's no dedicated UI shortcut for scripting this,
you POST the form directly, same login pattern as `bgw320_ippass.py`):

```python
import bgw320_ippass as b
import re, urllib.parse

with open(b.PASS_FILE) as f:
    b.login(f.read().strip())

html = b.curl([f'{b.ROUTER}/cgi-bin/dosprotect.ha'])
nonce = re.search(r'nonce[^>]+value="([^"]+)"', html).group(1)
body = urllib.parse.urlencode({
    'nonce': nonce,
    'downstream_echo_rqst_drop': 'off',
    'downstream_echo_rqst_drop_lan': 'on',
    'icmp_downstream_echo_rqst_drop_wan': 'on',
    'reflexive': 'on',  # or 'off'
    'algesp': 'off', 'algsip': 'on', 'Save': 'Save',
})
b.curl(['-X', 'POST', '-H', 'Content-Type: application/x-www-form-urlencoded',
        '--data', body, f'{b.ROUTER}/cgi-bin/dosprotect.ha'])
```

Verify by re-fetching the same page and checking the `reflexive` field.
The other fields above reflect one specific observed configuration —
check your own router's current values before reusing this rather than
assuming they'll match.

**On the router's own DoS log**: we found one instance where turning
Reflexive ACL On correlated with new "Other DoS attack" entries showing
up in the router's own event log (`cgi-bin/logs.ha`), then failed to
reproduce that correlation on a later, ~2-hour retest. Our take: the
log entries likely depend on actual external scanning/probe traffic
arriving, which is opportunistic, not guaranteed within any given
window — so neither result should be treated as final. Worth watching
your own router's log for a day or more if you want to actually settle
it for your situation.

## A second gotcha: default DHCP lease length on IP Passthrough

If you're using IP Passthrough (bridging a single device's IP straight
through, rather than NAT), the BGW320's default lease is short enough
that we saw it cause ~10-minute lease-cycling and rapid interface
flip-flops. Extending the lease (`dhcpday` in the IP Passthrough form)
fixed it. Note the web UI field has `maxlength="2"`, but that's
client-side only — POSTing a larger value directly (we used 7 days) is
accepted with no server-side rejection.

## The two scripts

- **`bgw320_ippass.py`** — logs in (nonce + MD5 challenge, matching the
  router's own login flow) and can report or set the IP Passthrough
  target MAC. Useful standalone (`status`/`ethernet`/`wifi`
  subcommands), and imported by the second script below for its
  login/session handling.
- **`capture_router_log.py`** — the router's own event log buffer is
  short-lived (observed as little as ~9 minutes / ~250 entries before
  older entries get silently evicted), with no push/webhook option.
  This polls it and appends new entries to a local file, so you have a
  persistent record instead of whatever the router happens to still be
  holding at the moment you check.

Both are configured via environment variables — see each script's
docstring. Neither embeds your router password; both read it from a
separate local file (`BGW320_PASS_FILE`, one line, not included here).

## What this isn't

This isn't a general Conduit/MoaV contribution — it's specific to this
one router model's login flow, firewall settings, and log format. If
you're on a different router, none of the code here will work as-is,
though the *shape* of the problem (a firewall setting silently blocking
inbound WebRTC, a router log buffer that evicts before you can read it)
may still be worth checking for on whatever you're running.
