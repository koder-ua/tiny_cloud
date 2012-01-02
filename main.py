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
import socket
import errno

import yaml

from easy_opt import YamlConfigOptParser, Opt, ExistingFileName

from common import CloudError
from vm import TinyCloud


class CloudOpts(YamlConfigOptParser):
    "Tool to manange small sets of vm's using libvirt"

    def_config_fname = 'cloud.yaml'

    cmd = Opt(nodash=True,
              choices=('start', 'stop', 'list', 'login', 'vms'),
              help="Commands: start - start vm or set; " + \
                         " stop - stop vm or set; " + \
                         " list - list running vms with ip adresses; " + \
                         " login - login to vm; " + \
                         " vms - list all available vms")

    vmnames = Opt(nodash=True, nargs='*', help="vm names", metavar="VMNAME")
    storage = Opt('-s', help="directory for temporary files", metavar="STORAGE")

    storage = Opt('-s', metavar="STORAGE", help="place for temporary files")

    uri = Opt('-u', metavar="URI", help="libvirt connection uri")

    template = ExistingFileName('-t',
                    help="XML file with vm template in libvirt format. " + \
                    "Without hdd, name, vcpu, memory and eth devs",
                    metavar="XML_FILE")

    config = ExistingFileName('-c', default="cloud.yaml",
                    help="Yaml file with vm descriptions", metavar="YAML_VMS_FILE")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]

    opts = CloudOpts.parse_opts(argv)

    print opts

    return 0

    cloud = yaml.load(open(opts.cloudconfig).read())
    raw_vms = cloud['vms']
    nets = cloud['networks']

    try:
        if opts.cmd == 'vms':
            cloud = TinyCloud(raw_vms, {}, None)
            print "\n".join(sorted(cloud))
        else:
            cloud = TinyCloud(raw_vms, nets, opts.url)

            if opts.cmd == 'start':
                for name in opts.vmnames:
                    cloud.start_vm(opts.template, name)
            elif opts.cmd == 'stop':
                for name in opts.vmnames:
                    cloud.stop_vm(name)
            elif opts.cmd == 'login':
                assert len(opts.vmnames) == 1
                cloud.login_to_vm(opts.vmnames[0])
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
                CloudOpts.print_help()
    except CloudError as err:
        print >>sys.stderr, err
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
