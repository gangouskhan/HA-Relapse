#!/usr/bin/with-contenv bashio
set -eu
# Read the actual host mapping so changing 8088 to 80 needs no source rebuild.
if declare -F bashio::app.port >/dev/null; then
    relapse_http_port=$(bashio::app.port '8080/tcp')
else
    relapse_http_port=$(bashio::addon.port '8080/tcp')
fi
if [[ ! "$relapse_http_port" =~ ^[0-9]{1,5}$ ]] || (( 10#$relapse_http_port < 1 || 10#$relapse_http_port > 65535 )); then
    bashio::log.fatal 'Enable a valid HTTP port for 8080/tcp in the app Network settings.'
    exit 1
fi
exec python3 -u /opt/relapse-host/host.py --public-http-port "$relapse_http_port"
