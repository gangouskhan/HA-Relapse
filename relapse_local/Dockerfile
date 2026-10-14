FROM ghcr.io/home-assistant/base:3.23

ARG BUILD_VERSION=0.4.7
ARG BUILD_ARCH
LABEL io.hass.name="HA Relapse" \
      io.hass.description="Local PS5 Relapse host and DNS" \
      io.hass.version="${BUILD_VERSION}" \
      io.hass.type="addon" \
      io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache python3 dnsmasq openssl
WORKDIR /opt/relapse-host
COPY host.py http_api.py payload_store.py UPSTREAM.json PAYLOAD_MENU.json ./
COPY admin ./admin
COPY www ./www
COPY run.sh /run.sh
RUN chmod 755 /run.sh
CMD ["/run.sh"]
