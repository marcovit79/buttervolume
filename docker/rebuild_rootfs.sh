#!/bin/bash

( cd ../../ && \
    zip -r buttervolume/docker/buttervolume.zip \
           buttervolume/buttervolume \
           buttervolume/CHANGES.rst \
           buttervolume/LICENSE \
           buttervolume/MANIFEST.in \
           buttervolume/README.rst \
           buttervolume/setup.py \
           buttervolume/test.py )
rm -rf rootfs
docker build -t rootfs . --no-cache
id=$(docker create rootfs true)
mkdir rootfs
docker export "$id" | tar -x -C rootfs
docker rm -vf "$id"
docker rmi rootfs

