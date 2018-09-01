FROM debian:stretch
MAINTAINER Marco Vit

RUN set -x; \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        btrfs-progs \
        curl \
        ca-certificates \
        python3-setuptools \
        ssh \
        unzip \
        rsync \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/docker/plugins \
    && mkdir -p /var/lib/buttervolume/{volumes,snapshots} \
    && mkdir /etc/buttervolume /root/.ssh

ENV VERSION master
ADD buttervolume.zip buttervolume.zip
RUN unzip buttervolume.zip \
    && rm buttervolume.zip \
    && cd buttervolume \
    && python3 setup.py install \
    && cd .. \
    && rm -rf buttervolume

COPY entrypoint.sh /
ENTRYPOINT ["/entrypoint.sh"]
CMD ["run"]
