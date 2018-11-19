#!/usr/bin/env python3
import sys
import argparse
import subprocess as sp
import os
import shutil
import pprint
import doit
import re
import logging
import time
import random
import string
from util.config import *
from util.util import *
import pathlib as pth
import tempfile

def main():
    parser = argparse.ArgumentParser(
        description="Build and run (in spike or qemu) boot code and disk images for firesim")
    parser.add_argument('-c', '--config',
                        help='Configuration file to use (defaults to br-disk.json)',
                        nargs='?', default=os.path.join(root_dir, 'workloads', 'br-disk.json'), dest='config_file')
    parser.add_argument('--workdir', help='Use a custom workload directory', default=os.path.join(root_dir, 'workloads'))
    parser.add_argument('-v', '--verbose',
                        help='Print all output of subcommands to stdout as well as the logs', action='store_true')
    subparsers = parser.add_subparsers(title='Commands', dest='command')

    # Build command
    build_parser = subparsers.add_parser(
        'build', help='Build an image from the given configuration.')
    build_parser.set_defaults(func=handleBuild)
    build_parser.add_argument('-j', '--job', nargs='?', default='all',
            help="Build only the specified JOB (defaults to 'all')")
    build_parser.add_argument('-i', '--initramfs', action='store_true', help="Build an image with initramfs instead of a disk")

    # Launch command
    launch_parser = subparsers.add_parser(
        'launch', help='Launch an image on a software simulator (defaults to qemu)')
    launch_parser.set_defaults(func=handleLaunch)
    launch_parser.add_argument('-s', '--spike', action='store_true',
            help="Use the spike isa simulator instead of qemu")
    launch_parser.add_argument('-j', '--job', nargs='?', default='all',
            help="Launch the specified job. Defaults to running the base image.")
    launch_parser.add_argument('-i', '--initramfs', action='store_true', help="Launch the initramfs version of this workload")

    # Init Command
    # XXX Not implemented yet: The plan is to make host_init only run when
    # specifically requested
    # init_parser = subparsers.add_parser(
    #         'init', help="Initialize workloads (using 'host_init' script)")
    # init_parser.set_defaults(func=handleInit)

    args = parser.parse_args()
    args.config_file = os.path.abspath(args.config_file)

    initLogging(args)
    log = logging.getLogger()

    # Load all the configs from the workload directory
    cfgs = ConfigManager([os.path.abspath(args.workdir)])
    targetCfg = cfgs[args.config_file]
    
    if args.initramfs:
        targetCfg['initramfs'] = True
        if 'jobs' in targetCfg:
            for j in targetCfg['jobs'].values():
                j['initramfs'] = True

    # Jobs are named with their base config internally 
    if args.command == 'build' or args.command == 'launch':
        if args.job != 'all':
            if 'jobs' in targetCfg: 
                args.job = targetCfg['name'] + '-' + args.job
            else:
                print("Job " + args.job + " requested, but no jobs specified in config file\n")
                parser.print_help()

    args.func(args, cfgs)

class doitLoader(doit.cmd_base.TaskLoader):
    workloads = []

    def load_tasks(self, cmd, opt_values, pos_args):
        task_list = [doit.task.dict_to_task(w) for w in self.workloads]
        config = {'verbosity': 2}
        return task_list, config

def addDep(loader, config):

    # Add a rule for the binary
    file_deps = []
    task_deps = []
    if 'linux-config' in config:
        file_deps.append(config['linux-config'])

    loader.workloads.append({
            'name' : config['bin'],
            'actions' : [(makeBin, [config])],
            'targets' : [config['bin']],
            'file_dep': file_deps,
            'task_dep' : task_deps
            })

    # Add a rule for the initramfs version if requested
    # Note that we need both the regular bin and initramfs bin if the base
    # workload needs an init script
    if 'initramfs' in config:
        file_deps = [config['img']]
        task_deps = [config['img']]
        if 'linux-config' in config:
            file_deps.append(config['linux-config'])

        loader.workloads.append({
                'name' : config['bin'] + '-initramfs',
                'actions' : [(makeBin, [config], {'initramfs' : True})],
                'targets' : [config['bin'] + '-initramfs'],
                'file_dep': file_deps,
                'task_dep' : task_deps
                })

    # Add a rule for the image (if any)
    file_deps = []
    task_deps = []
    if 'img' in config:
        if 'base-img' in config:
            task_deps = [config['base-img']]
            file_deps = [config['base-img']]
        if 'files' in config:
            for fSpec in config['files']:
                # Add directories recursively
                if os.path.isdir(fSpec.src):
                    for root, dirs, files in os.walk(fSpec.src):
                        for f in files:
                            file_deps.append(os.path.join(root, f))
                else:
                    file_deps.append(fSpec.src)			
        if 'init' in config:
            file_deps.append(config['init'])
            task_deps.append(config['bin'])
        if 'runSpec' in config and config['runSpec'].path != None:
            file_deps.append(config['runSpec'].path)
        if 'cfg-file' in config:
            file_deps.append(config['cfg-file'])
        
        loader.workloads.append({
            'name' : config['img'],
            'actions' : [(makeImage, [config])],
            'targets' : [config['img']],
            'file_dep' : file_deps,
            'task_dep' : task_deps
            })

# Generate a task-graph loader for the doit "Run" command
# Note: this doesn't depend on the config or runtime args at all. In theory, it
# could be cached, but I'm not going to bother unless it becomes a performance
# issue.
def buildDepGraph(cfgs):
    loader = doitLoader()

    # Define the base-distro tasks
    for d in distros:
        dCfg = cfgs[d]
        if 'img' in dCfg:
            loader.workloads.append({
                    'name' : dCfg['img'],
                    'actions' : [(dCfg['builder'].buildBaseImage, [])],
                    'targets' : [dCfg['img']],
                    'uptodate': [(dCfg['builder'].upToDate, [])]
                })

    # Non-distro configs 
    for cfgPath in (set(cfgs.keys()) - set(distros)):
        config = cfgs[cfgPath]
        addDep(loader, config)

        if 'jobs' in config.keys():
            for jCfg in config['jobs'].values():
                addDep(loader, jCfg)

    return loader

def handleBuild(args, cfgs):
    loader = buildDepGraph(cfgs)
    config = cfgs[args.config_file]
    binList = [config['bin']]
    imgList = []
    if 'img' in config:
        imgList.append(config['img'])

    if 'initramfs' in config:
        binList.append(config['bin'] + '-initramfs')

    if 'jobs' in config.keys():
        if args.job == 'all':
            for jCfg in config['jobs'].values():
                binList.append(jCfg['bin'])
                if 'initramfs' in jCfg:
                    binList.append(jCfg['bin'] + '-initramfs')
                if 'img' in jCfg:
                    imgList.append(jCfg['img'])
        else:
            jCfg = config['jobs'][args.job]
            binList.append(jCfg['bin'])
            if 'initramfs' in jCfg:
                binList.append(jCfg['bin'] + '-initramfs')
            if 'img' in jCfg:
                imgList.append(jCfg['img'])

    # The order isn't critical here, we should have defined the dependencies correctly in loader 
    doit.doit_cmd.DoitMain(loader).run(binList + imgList)

def launchSpike(config, initramfs=False):
    log = logging.getLogger()
    if initramfs or 'img' not in config:
        sp.check_call(['spike', '-p4', '-m4096', config['bin'] + '-initramfs'])
    else:
        raise ValueError("Spike does not support disk-based configurations")

def launchQemu(config, initramfs=False):
    log = logging.getLogger()

    if initramfs:
        exe = config['bin'] + '-initramfs'
    else:
        exe = config['bin']

    cmd = ['qemu-system-riscv64',
           '-nographic',
           '-smp', '4',
           '-machine', 'virt',
           '-m', '4G',
           '-kernel', exe,
           '-object', 'rng-random,filename=/dev/urandom,id=rng0',
           '-device', 'virtio-rng-device,rng=rng0',
           '-device', 'virtio-net-device,netdev=usernet',
           '-netdev', 'user,id=usernet,hostfwd=tcp::10000-:22']

    if 'img' in config and not initramfs:
        cmd = cmd + ['-device', 'virtio-blk-device,drive=hd0',
                     '-drive', 'file=' + config['img'] + ',format=raw,id=hd0']
        cmd = cmd + ['-append', 'ro root=/dev/vda']

    sp.check_call(cmd)

def handleLaunch(args, cfgs):
    log = logging.getLogger()
    baseConfig = cfgs[args.config_file]
    if 'jobs' in baseConfig.keys() and args.job != 'all':
        # Run the specified job
        config = cfgs[args.config_file]['jobs'][args.job]
    else:
        # Run the base image
        config = cfgs[args.config_file]
    
    if args.spike:
        if 'img' in config and 'initramfs' not in config:
            sys.exit("Spike currently does not support disk-based " +
                    "configurations. Please use an initramfs based image.")
        launchSpike(config, args.initramfs)
    else:
        launchQemu(config, args.initramfs)

def handleInit(args, cfgs):
    config = cfgs[args.config_file]
    if 'host_init' in config:
        run([config['host_init']], cwd=config['workdir'])

# Now build linux/bbl
def makeBin(config, initramfs=False):
    log = logging.getLogger()

    # We assume that if you're not building linux, then the image is pre-built (e.g. during host-init)
    if 'linux-config' in config:
        linuxCfg = os.path.join(linux_dir, '.config')
        shutil.copy(config['linux-config'], linuxCfg)

        if initramfs:
            with tempfile.NamedTemporaryFile(suffix='.cpio') as tmpCpio:
                toCpio(config, config['img'], tmpCpio.name)
                convertInitramfsConfig(linuxCfg, tmpCpio.name)
                run(['make', 'ARCH=riscv', 'olddefconfig'], cwd=linux_dir)
                run(['make', 'ARCH=riscv', 'vmlinux', jlevel], cwd=linux_dir)
        else: 
            run(['make', 'ARCH=riscv', 'vmlinux', jlevel], cwd=linux_dir)

        if not os.path.exists('riscv-pk/build'):
            os.mkdir('riscv-pk/build')

        run(['../configure', '--host=riscv64-unknown-elf',
            '--with-payload=../../riscv-linux/vmlinux'], cwd='riscv-pk/build')
        run(['make', jlevel], cwd='riscv-pk/build')

        if initramfs:
            shutil.copy('riscv-pk/build/bbl', config['bin'] + '-initramfs')
        else:
            shutil.copy('riscv-pk/build/bbl', config['bin'])
    elif config['distro'] != 'bare':
        raise ValueError("No linux config defined. This is only supported for workloads based on 'bare'")

def makeImage(config):
    log = logging.getLogger()

    shutil.copy(config['base-img'], config['img'])
    
    if 'host_init' in config:
        log.info("Applying host_init: " + config['host_init'])
        if not os.path.exists(config['host_init']):
            raise ValueError("host_init script " + config['host_init'] + " not found.")

        run([config['host_init']], cwd=config['workdir'])

    if 'files' in config:
        log.info("Applying file list: " + str(config['files']))
        applyFiles(config['img'], config['files'])

    if 'init' in config:
        log.info("Applying init script: " + config['init'])
        if not os.path.exists(config['init']):
            raise ValueError("Init script " + config['init'] + " not found.")

        # Apply and run the init script
        init_overlay = config['builder'].generateBootScriptOverlay(config['init'])
        applyOverlay(config['img'], init_overlay)
        print("Launching: " + config['bin'])
        launchQemu(config)

        # Clear the init script
        run_overlay = config['builder'].generateBootScriptOverlay(None)
        applyOverlay(config['img'], run_overlay)

    if 'runSpec' in config:
        spec = config['runSpec']
        if spec.command != None:
            log.info("Applying run command: " + spec.command)
            scriptPath = genRunScript(spec.command)
        else:
            log.info("Applying run script: " + spec.path)
            scriptPath = spec.path

        if not os.path.exists(scriptPath):
            raise ValueError("Run script " + scriptPath + " not found.")

        run_overlay = config['builder'].generateBootScriptOverlay(scriptPath)
        applyOverlay(config['img'], run_overlay)

# Apply the overlay directory "overlay" to the filesystem image "img"
# Note that all paths must be absolute
def applyOverlay(img, overlay):
    log = logging.getLogger()
    applyFiles(img, [FileSpec(src=os.path.join(overlay, "*"), dst='/')])
    
# Copies a list of type FileSpec ('files') into the destination image (img)
def applyFiles(img, files):
    log = logging.getLogger()

    run(['sudo', 'mount', '-o', 'loop', img, mnt])
    try:
        for f in files:
            # Overlays may not be owned by root, but the filesystem must be.
            # Rsync lets us chown while copying.
            # Note: shell=True because f.src is allowed to contain globs
            # Note: os.path.join can't handle overlay-style concats (e.g. join('foo/bar', '/baz') == '/baz')
            run('sudo rsync -a --chown=root:root ' + f.src + " " + os.path.normpath(mnt + f.dst), shell=True)
    finally:
        run(['sudo', 'umount', mnt])

main()
