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

import yaml

from easy_opt import YamlConfigOptParser, Opt, ExistingFileName, DictOpt, IntOpt

from common import CloudError
from vm import TinyCloud
from utils import logger, logger_handler


class CloudOpts(YamlConfigOptParser):
    "Tool to manange small sets of vm's using libvirt"

    def_config_fname = 'cloud.yaml'

    cmd = Opt(nodash=True,
              choices=('start', 'stop', 'list', 'login', 'vms', 'wait_ip', 'wait_ssh'),
              help="Commands: start - start vm or set; " + \
                         " stop - stop vm or set; " + \
                         " list - list running vms with ip adresses; " + \
                         " login - login to vm; " + \
                         " vms - list all available vms")

    wait_time = IntOpt(default=10, help="timeout to wait till vm gets ip/ssh", metavar="TIME")

    vmnames = Opt(nodash=True, nargs='*', help="vm names", metavar="VMNAME")
    storage = Opt('-s', help="directory for temporary files", metavar="STORAGE")

    config = ExistingFileName('-c', default="cloud.yaml",
                    help="Yaml file with vm descriptions", metavar="YAML_VMS_FILE")

    users = DictOpt('-p', help="Credentials - uname:passwd[,uname:passwd[,...]]")
    log_level = Opt(help="Set log level", metavar='LOGLEVEL', default='ERROR')

def get_default_config(cfg_fname=None):
    if cfg_fname is None:
        cfg_fname = os.path.join(os.path.dirname(__file__), 'cloud.yaml')
    return yaml.load(open(cfg_fname).read())

def cloud_connect(cfg_fname=None):
    cloud_cfg = get_default_config(cfg_fname)
    return TinyCloud(vms=cloud_cfg['vms'], 
                     templates=cloud_cfg['templates'], 
                     networks=cloud_cfg['networks'], 
                     urls=cloud_cfg['urls'])

def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]

    opts = CloudOpts.parse_opts(argv)

    #print opts
    #return 0

    logger.setLevel(getattr(logging, opts.log_level))
    logger_handler.setLevel(getattr(logging, opts.log_level))

    try:
        cloud = cloud_connect(opts.config)
        if opts.cmd == 'vms':
            print "\n".join(sorted(cloud))
        else:
            if opts.cmd == 'start':
                for name in opts.vmnames:
                    cloud.start_vm(name, opts.users)
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
                    print "{0:>5} {1:<15} => {2}".format(domain.ID(), domain.name(), all_ips)
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
                            print "{0:<15} => {1}".format(vmname, " ".join(ips))
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
                            print "VM {0} don't start ssh server in time".format(vmname)
                            return 1

                        time.sleep(0.01)

            else:
                print >>sys.stderr, "Error : Unknown cmd {0}".format(opts.cmd)
                CloudOpts.print_help()
    except CloudError as err:
        print >>sys.stderr, err
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
