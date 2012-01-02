"""command line support module for tiny cloud library"""

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

import sys
import socket
import errno
import argparse

import yaml

from common import CloudError
from vm import TinyCloud


def build_parser():
    parser = argparse.ArgumentParser(description="Tool to manange small sets of vm's using libvirt")

    parser.add_argument('cmd',
                        choices=('start', 'stop', 'list', 'login', 'vms'),
                        help="Commands: start - start vm or set; " + \
                             " stop - stop vm or set; " + \
                             " list - list running vms with ip adresses; " + \
                             " login - login to vm; " + \
                             " vms - list all available vms")
    parser.add_argument('-n', '--name', help="vm name", metavar="VMNAME")
    parser.add_argument('-u', '--url', default="qemu:///system",
                        metavar="URL", help="libvirt connection url")
    parser.add_argument('-t', '--template', default="vm.xml",
                        help="XML file with vm template in libvirt format. " + \
                        "Without hdd, name, vcpu, memory and eth devs",
                        metavar="XML_FILE")
    parser.add_argument('-v', '--vms', default="vms.yaml",
                        help="Yaml file with vm descriptions", metavar="YAML_VMS_FILE")
    parser.add_argument('-e', '--networks', default="networks.yaml",
                        help="Yaml file with netwokrs descriptions", metavar="YAML_NET_FILE")

    return parser


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]

    parser = build_parser()
    opts = parser.parse_args(argv)

    raw_vms = yaml.load(open(opts.vms).read())
    nets = yaml.load(open(opts.networks).read())

    try:
        if opts.cmd == 'vms':
            cloud = TinyCloud(raw_vms, {}, None)
            print "\n".join(sorted(cloud))
        else:
            cloud = TinyCloud(raw_vms, nets, opts.url)

            if opts.cmd == 'start':
                cloud.start_vm(opts.template, opts.name)
            elif opts.cmd == 'stop':
                cloud.stop_vm(opts.name)
            elif opts.cmd == 'login':
                cloud.login_to_vm(opts.name)
            elif opts.cmd == 'list':
                for domain in cloud.list_vms():
                    try:
                        all_ips = ", ".join(cloud.get_vm_ips(domain.name()))
                    except socket.error as err:
                        if err.errno != errno.EPERM:
                            raise
                        all_ips = "Not enought permissions for arp-scan"
                    print "{0:>5} {1:<15} => {2}".format(domain.ID(), domain.name(), all_ips)
            else:
                print >>sys.stderr, "Error : Unknown cmd {0}".format(opts.cmd)
                parser.print_help()
    except CloudError as err:
        print >>sys.stderr, err
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
