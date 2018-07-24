#!/bin/bash
set -e

if [ $# -ne 0 ]; then
  PLATFORM=$1
  if [ $1 == "x86" ]; then
    LINUX_CONFIG=linux-config-x86
  elif [ $1 == "fedora" ]; then
    LINUX_CONFIG=linux-config-fedora
  elif [ $1 == "initramfs" ]; then
    LINUX_CONFIG=linux-config-initramfs
  else
    echo "Please provide a valid platform (or no arguments to default to firesim)"
    exit 1
  fi
else
  PLATFORM="initramfs"
  LINUX_CONFIG=linux-config-initramfs
fi

LINUX_SRC=${PWD}/riscv-linux

# Update the overlay with pfa_tests
pushd pfa_tests/
pushd qsort/
rm -f qsort
make
popd
pushd unit/
rm -f unit
make
popd
pushd util/
make
popd
popd
mkdir -p buildroot-overlay/root
rm -rf buildroot-overlay/root/*
rm -rf buildroot/output/target/root/*
cp -r pfa_tests/* buildroot-overlay/root/

# overwrite buildroot's config with ours, then build rootfs
cp buildroot-config buildroot/.config
pushd buildroot
# Note: Buildroot doesn't support parallel make
make -j1
popd
cp buildroot/output/images/rootfs.ext2 ./rootfs0.ext2
cp buildroot/output/images/rootfs.cpio .

cp $LINUX_CONFIG riscv-linux/.config
pushd $LINUX_SRC
make -j16 ARCH=riscv vmlinux
popd

# build pk, provide vmlinux as payload
pushd riscv-pk
mkdir -p build
pushd build
../configure --host=riscv64-unknown-elf --with-payload=$LINUX_SRC/vmlinux
make -j16
cp bbl ../../bbl-vmlinux
popd
popd

if [ $PLATFORM == "firesim" ]; then
  # make 7 more copies of the rootfs for f1.16xlarge nodes
  for i in {1..7}
  do
      cp rootfs0.ext2 rootfs$i.ext2
  done
fi
