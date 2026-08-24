#!/bin/bash
# bgw320_conduit_watchdog.sh — periodic check that a BGW320's IP Passthrough
# binding actually matches the interface you're using, and repairs both the
# router side and the local interface if they've drifted apart. Meant to be
# called from a broader health-check loop (e.g. conduit-monitor, or your own
# cron/LaunchAgent), not run as a standalone daemon itself.
#
# Requires bgw320_ippass.py in the same directory (see that script's own
# docstring for BGW320_* login/config variables — this script reuses it for
# all router interaction, it doesn't talk to the router directly itself).
#
# Untested on the BGW620: that's a different gateway generation (CommScope/
# Vantiva hardware, not the same lineage as the BGW320) — the admin page
# layout, login flow, and form fields this depends on may or may not match.
# If you've verified it works (or doesn't) on a BGW620, that's worth
# reporting back.
#
# WHY TWO SEPARATE CHECKS, NOT ONE
#
# The router's own IP Passthrough setting and your local interface's actual
# IP address can drift apart independently — correcting one does not
# retroactively fix the other:
#
#   - The router can be misconfigured (Passthrough bound to the wrong MAC)
#     while your interface still shows an old, previously-valid public IP
#     from before the mismatch happened — a stale DHCP lease masking a
#     real, current problem.
#   - The router's binding can be entirely correct while your local
#     interface is still stuck on a stale private-IP lease from before the
#     last time it associated (a DHCP race during a network transition) —
#     the router side looks fine, but Conduit still can't accept inbound
#     connections properly.
#
# Both of these were found live, independently, in the setup this script
# was extracted from. Checking only one side reliably misses the other.
#
# ============================================================================
# CONFIGURATION (environment variables)
# ============================================================================
#   BGW320_TARGET              REQUIRED. A short label for what this
#                               interface should be, e.g. "ethernet" or
#                               "wifi" — passed straight through to
#                               bgw320_ippass.py's own ethernet/wifi
#                               subcommands, so it must match how that
#                               script expects to be called.
#   BGW320_EXPECTED_MAC         REQUIRED. The MAC address IP Passthrough
#                               should be bound to for this target (i.e.
#                               BGW320_ETHERNET_MAC or BGW320_WIFI_MAC,
#                               whichever matches BGW320_TARGET — see
#                               bgw320_ippass.py).
#   BGW320_LOCAL_INTERFACE      REQUIRED. The local network interface name
#                               to check/bounce, e.g. "en0".
#   BGW320_LOCAL_INTERFACE_IS_WIFI  true/false (default: true). Determines
#                               whether a stale-IP bounce uses
#                               `networksetup -setairportpower` (WiFi) or
#                               `-setnetworkserviceenabled` (wired).
#   BGW320_IPPASS_SCRIPT         path to bgw320_ippass.py (default: same
#                               directory as this script).
#   CONDUIT_SERVICE_LABEL       launchd label for Conduit, restarted after
#                               a repair (default: ca.psiphon.conduit).
#
# Prints one line per action taken (or nothing if everything was already
# correct) — designed to be captured by a caller and folded into its own
# logging, same pattern as rotate-log.sh.
# ============================================================================

BGW320_TARGET="${BGW320_TARGET:?BGW320_TARGET must be set (e.g. ethernet or wifi)}"
BGW320_EXPECTED_MAC="${BGW320_EXPECTED_MAC:?BGW320_EXPECTED_MAC must be set}"
BGW320_LOCAL_INTERFACE="${BGW320_LOCAL_INTERFACE:?BGW320_LOCAL_INTERFACE must be set (e.g. en0)}"
BGW320_LOCAL_INTERFACE_IS_WIFI="${BGW320_LOCAL_INTERFACE_IS_WIFI:-true}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BGW320_IPPASS_SCRIPT="${BGW320_IPPASS_SCRIPT:-$SCRIPT_DIR/bgw320_ippass.py}"
CONDUIT_SERVICE_LABEL="${CONDUIT_SERVICE_LABEL:-ca.psiphon.conduit}"

# Bounces BGW320_LOCAL_INTERFACE and restarts Conduit if it's on no address
# at all, a self-assigned 169.254.x link-local, or a private range (192.168.,
# 10., or 172.16-31.) — any of which mean it isn't actually using the public
# IP Passthrough is supposed to be giving it, whether or not the router-side
# binding itself is currently correct.
fix_stale_local_ip() {
    local ip
    ip=$(ipconfig getifaddr "$BGW320_LOCAL_INTERFACE" 2>/dev/null)
    if [ -n "$ip" ] && ! echo "$ip" | grep -qE "^(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|169\.254\.)"; then
        return   # real, non-private address — nothing to do
    fi
    if [ "$BGW320_LOCAL_INTERFACE_IS_WIFI" = "true" ]; then
        # `networksetup -setairportpower` does NOT validate that its
        # argument is actually a WiFi device -- given an unrecognized
        # interface name, it silently falls back to controlling whatever
        # WiFi interface *does* exist on the system instead of erroring.
        # Found live (260824): testing with a deliberately-wrong interface
        # name toggled the real WiFi connection instead of failing loudly.
        # Refuse to proceed unless BGW320_LOCAL_INTERFACE is confirmed to
        # actually be a Wi-Fi hardware port first.
        local wifi_device
        wifi_device=$(networksetup -listallhardwareports | awk '/Hardware Port: Wi-Fi/{getline; print $2}')
        if [ "$wifi_device" != "$BGW320_LOCAL_INTERFACE" ]; then
            echo "BGW320_LOCAL_INTERFACE=$BGW320_LOCAL_INTERFACE is not this system's actual Wi-Fi device (that's $wifi_device) -- refusing to touch airport power to avoid affecting the wrong interface, needs a manual look"
            return
        fi
        networksetup -setairportpower "$BGW320_LOCAL_INTERFACE" off
        sleep 5
        networksetup -setairportpower "$BGW320_LOCAL_INTERFACE" on
    else
        local service
        service=$(networksetup -listallhardwareports | awk -v dev="$BGW320_LOCAL_INTERFACE" '/Hardware Port:/{name=$0; sub(/Hardware Port: /,"",name)} $0=="Device: " dev {print name}')
        if [ -z "$service" ]; then
            echo "could not find network service name for $BGW320_LOCAL_INTERFACE, cannot bounce it — needs a manual look"
            return
        fi
        networksetup -setnetworkserviceenabled "$service" off
        sleep 3
        networksetup -setnetworkserviceenabled "$service" on
    fi
    sleep 12
    launchctl kickstart -k "gui/$(id -u)/${CONDUIT_SERVICE_LABEL}" 2>/dev/null
    echo "$BGW320_LOCAL_INTERFACE had no usable public IP (was: ${ip:-none}), bounced it and restarted Conduit"
}

CURRENT_MAC=""
for attempt in 1 2 3; do
    CURRENT_MAC=$(python3 "$BGW320_IPPASS_SCRIPT" status 2>/dev/null | grep -oE "CURRENT_MAC=[0-9a-f:]+" | cut -d= -f2)
    [ -n "$CURRENT_MAC" ] && break
    sleep 5
done

if [ -z "$CURRENT_MAC" ]; then
    echo "could not read router IP Passthrough state after 3 attempts — router may be unreachable, needs a look"
    exit 1
fi

if [ "$CURRENT_MAC" != "$BGW320_EXPECTED_MAC" ]; then
    corrected="no"
    for attempt in 1 2 3; do
        if python3 "$BGW320_IPPASS_SCRIPT" "$BGW320_TARGET" >/dev/null 2>&1; then
            corrected="yes"
            break
        fi
        sleep 15
    done
    if [ "$corrected" = "yes" ]; then
        NOTE=$(fix_stale_local_ip)
        echo "router IP Passthrough was bound to $CURRENT_MAC, corrected to $BGW320_TARGET ($BGW320_EXPECTED_MAC)${NOTE:+; $NOTE}"
    else
        echo "router IP Passthrough is bound to $CURRENT_MAC but should be $BGW320_TARGET ($BGW320_EXPECTED_MAC), failed to correct after 3 attempts — needs a manual look"
        exit 1
    fi
else
    # Router's own binding is already correct — but the local interface can
    # still be on a stale IP independently of that (see the header comment
    # for why this needs checking even when the router side looks fine).
    NOTE=$(fix_stale_local_ip)
    [ -n "$NOTE" ] && echo "router IP Passthrough MAC was already correct, but local interface needed a repair: $NOTE"
fi
