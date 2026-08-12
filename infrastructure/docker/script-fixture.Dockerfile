# A Git server for verify to talk to. verify cannot be demonstrated or tested
# without one, and depending on a public host would put the internet on the
# critical path of `make dev`.
#
# Debian rather than Alpine: git-http-backend is not packaged for Alpine, and
# it is what serves the smart HTTP protocol a partial clone needs.
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git nginx fcgiwrap spawn-fcgi apache2-utils curl \
    && rm -rf /var/lib/apt/lists/*

COPY images/script-fixture/nginx.conf /etc/nginx/nginx.conf
COPY images/script-fixture/entrypoint.sh /entrypoint.sh
COPY images/script-fixture/repo /seed/repo
RUN chmod +x /entrypoint.sh

EXPOSE 80
CMD ["/entrypoint.sh"]
