#!/usr/bin/env python
# Copyright (C) 2011-2012 Kostiantyn Danylov aka koder <koder.mail@gmail.com>
#
# This file is part of tiny_cloud library.
#
# tiny_cloud is free software; you can redistribute it and/or modify it under the
# terms of the GNU Lesser General Public License as published by the Free
# Software Foundation; either version 2.1 of the License, or (at your option)
# any later version.
#
# tiny_cloud is distrubuted in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with tiny_cloud; if not, write to the Free Software Foundation, Inc.,
# 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA.

"""command line support module for tiny cloud library"""

import sys
import time
import errno
import socket
import os.path
import logging
import argparse

import yaml

from vm import TinyCloud
from common import CloudError
from utils import logger, logger_handler


def get_default_config(cfg_fname=None):
    if cfg_fname is None:
        cfg_fname = os.path.join(os.path.dirname(__file__), 'cloud.yaml')

        if not os.path.isfile(cfg_fname):
            cfg_fname = os.path.expanduser("~/.tcloud/cloud.yaml")

    with open(cfg_fname) as fd:
        cfg = yaml.load(fd.read())

    cfg['cfg_folder'] = os.path.dirname(cfg_fname)

    return cfg


def cloud_connect(cfg_fname=None):
    cloud_cfg = get_default_config(cfg_fname)
    return TinyCloud(vms=cloud_cfg['vms'],
                     templates=cloud_cfg['templates'],
                     networks=cloud_cfg['networks'],
                     urls=cloud_cfg['urls'],
                     root=cloud_cfg['cfg_folder'])


def create_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', default=None)
    parser.add_argument('-p', '--prepare', action="store_true", default=False)
    parser.add_argument('-l', '--loglevel', default="ERROR")
    parser.add_argument('-w', '--wait_time', default=30, type=int)
    parser.add_argument('cmd', choices=['start', 'stop', 'list',
                                        'login', 'vms', 'wait_ip', 'wait_ssh'])
    parser.add_argument('vmnames', nargs='*')
    return parser


def main(argv=None):
    opts = create_parser().parse_args(argv)
    opts.users = None

    logger.setLevel(getattr(logging, opts.loglevel))
    logger_handler.setLevel(getattr(logging, opts.loglevel))

    try:
        cloud = cloud_connect(opts.config)
        if opts.cmd == 'vms':
            print "\n".join(sorted(cloud))
        else:
            if opts.cmd == 'start':
                for name in opts.vmnames:
                    cloud.start_vm(name, opts.users, opts.prepare)
            elif opts.cmd == 'stop':
                for name in opts.vmnames:
                    cloud.stop_vm(name, timeout1=opts.wait_time)
            elif opts.cmd == 'login':
                assert len(opts.vmnames) == 1
                cloud.login_to_vm(opts.vmnames[0], opts.users)
            elif opts.cmd == 'list':
                for domain in cloud.list_vms():
                    try:
                        all_ips = ", ".join(cloud.get_vm_ips(domain.name()))
                    except socket.error as err:
                        if err.errno != errno.EPERM:
                            raise
                        all_ips = "Not enought permissions for arp-scan"
                    print "{0:>5} {1:<15} => {2}".format(domain.ID(),
                                                         domain.name(),
                                                         all_ips)
            elif opts.cmd == 'wait_ip':
                tend = time.time() + opts.wait_time
                for vmname in opts.vmnames:
                    while True:
                        try:
                            ips = list(cloud.get_vm_ips(vmname))
                        except socket.error as err:
                            if err.errno != errno.EPERM:
                                raise
                            print "Not enought permissions for arp-scan"
                            return 1

                        if len(ips) != 0:
                            print "{0:<15} => {1}".format(vmname,
                                                          " ".join(ips))
                            break

                        if time.time() >= tend:
                            print "VM {0} don't get ip in time".format(vmname)
                            return 1

                        time.sleep(0.01)

            elif opts.cmd == 'wait_ssh':
                tend = time.time() + opts.wait_time
                for vmname in opts.vmnames:
                    while True:
                        try:
                            ip = cloud.get_vm_ssh_ip(vmname)
                        except socket.error as err:
                            if err.errno != errno.EPERM:
                                raise
                            print "Not enought permissions for arp-scan"
                            return 1

                        if ip is not None:
                            print "{0:<15} => {1}".format(vmname, ip)
                            break

                        if time.time() >= tend:
                            templ = "VM {0} don't start ssh server in time"
                            print templ.format(vmname)
                            return 1

                        time.sleep(0.01)

            else:
                print >>sys.stderr, "Error : Unknown cmd {0}".format(opts.cmd)
    except CloudError as err:
        print >>sys.stderr, err
        return 1
    return 0

if __name__ == "__main__":
    exit(main(sys.argv[1:]))
