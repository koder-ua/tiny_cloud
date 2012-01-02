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

import re
import time
from xml.etree.ElementTree import fromstring, tostring, Element

import libvirt

import xmlbuilder

from network import login_ssh, get_vm_ips, get_vm_ssh_ip, ifconfig, get_network_bridge
from utils import ip2int, int2ip, netsz2netmask, netmask2netsz, logger
from common import CloudError
from disk_image import prepare_guest


#suppress libvirt error messages to console
libvirt.registerErrorHandler(lambda x, y: 1, None)


class VM(object):
    eth_re = re.compile(r"eth\d+")

    class NetParams(object):
        mac_re = re.compile(r"\d\d:\d\d:\d\d:\d\d:\d\d:\d\d")
        ip_re = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}")
        network_re = re.compile(r"[a-zA-Z_][-\w_]*")

    def __init__(self, name, **keys):
        self.name = name
        self.mem = int(keys.pop('mem', 1024))
        self.vcpu = int(keys.pop('vcpu', 1))

        credentials = keys.pop('credentials', 'root:root')
        self.user, self.passwd = credentials.split(':')
        self.image = keys.pop('image')

        self.__dict__.update(keys)

    def eths(self):
        for k, v in self.__dict__.items():
            if self.eth_re.match(k):
                params = v.split(',')
                res = {'name': k,
                       'network': 'default'}
                for param in params:
                    param = param.strip()
                    ok = False
                    for k, v in self.NetParams.__dict__.items():
                        if k.endswith('_re'):
                            if v.match(param):
                                res[k[:-len('_re')]] = param
                                ok = True
                                break
                    if not ok:
                        raise ValueError("Can't categorize network parameter {0!r}".format(param))
                yield res

    def __str__(self):
        return "VM({0!r})".format(self.name)

    def __repr__(self):
        return str(self)


class Network(object):
    def __init__(self, name, **data):
        ip_range = data['range']
        self.ip1, ip2_sz = ip_range.split('-')
        self.ip2, self.sz = ip2_sz.split('/')
        self.ip1 = self.ip1.strip()
        self.ip2 = self.ip2.strip()
        self.sz = int(self.sz)

        self.name = name
        self.ip = int2ip(ip2int(self.ip1) + 1)
        self.bridge = data['bridge'].strip()
        self.netmask = netsz2netmask(self.sz)


class TinyCloud(object):
    def __init__(self, vms, networks, conn):
        if isinstance(conn, basestring):
            conn = libvirt.open(conn)
        self.conn = conn
        self.vms = {}
        self.add_vms(vms)
        self.networks = [Network(name, **data) for name, data in networks.items()]
        logger.debug("Cloud with {0} vm templates created".format(self.vms.keys()))

    DOM_SEPARATOR = '.'

    def add_vms(self, vms, prefix=""):
        for k, v in vms.items():
            if not isinstance(v, dict):
                continue

            if v.get('type', 'vm') == 'network':
                self.add_vms(v, prefix=prefix + k + self.DOM_SEPARATOR)
            else:
                self.vms[prefix + k] = VM(prefix + k, **v)

    def __iter__(self):
        return iter(self.vms)

    def get_vm_ssh_ip(self, vmname):
        return get_vm_ssh_ip(self.conn, vmname)

    def get_vm_ips(self, vmname):
        return get_vm_ips(self.conn, vmname)

    def start_net(self, name):
        logger.info("Start network " + name)
        try:
            net = self.conn.networkLookupByName(name)
            if not net.isActive():
                logger.debug("Network registered in libvirt - start it")
                net.create()
            else:
                logger.debug("Network already active")

        except libvirt.libvirtError:
            try:
                logger.debug("No such network in libvirt")
                net = self.networks[name]
            except KeyError:
                msg = "Can't found network {0!r}".format(name)
                logger.error(msg)
                raise CloudError(msg)

            xml = xmlbuilder.XMLBuilder('network')
            xml.name(name)
            xml.bridge(name=net.bridge)
            with xml.ip(address=net.ip, netmask=net.netmask):
                xml.dhcp.range(start=net.ip1, end=net.ip2)

            logger.debug("Create network")
            self.conn.networkCreateXML(str(xml))

    def start_vm(self, template, vmname, users):
        logger.info("Start vm/network {0} from template {1} with credentials {2}".format(vmname, template, users))

        vm_xml_templ = open(template).read()

        vms = [vm for vm in self.vms.values()
                if vm.name == vmname or
                    vm.name.startswith(vmname +
                                        self.DOM_SEPARATOR)]
        vm_names = " ".join(vm.name for vm in vms)
        logger.debug("Found next vm's, which match name glob {0}".format(vm_names))

        for vm in vms:
            logger.debug("Prepare vm {0}".format(vm.name))
            vm_xm = fromstring(vm_xml_templ)

            el = Element('vcpu')
            el.text = str(vm.vcpu)
            vm_xm.append(el)

            el = Element('name')
            el.text = vm.name
            vm_xm.append(el)

            el = Element('memory')
            el.text = str(vm.mem * 1024)
            vm_xm.append(el)

            devs = vm_xm.find('devices')

            hdd = xmlbuilder.XMLBuilder('disk', device='disk', type='file')
            hdd.driver(name='qemu', type='qcow2')
            hdd.source(file=vm.image)
            hdd.target(bus='ide', dev='hda')

            devs.append(~hdd)

            eths = {}

            for eth in vm.eths():
                edev = xmlbuilder.XMLBuilder('interface', type='network')
                edev.source(network=eth['network'])
                edev.mac(address=eth['mac'])
                devs.append(~edev)

                if 'ip' not in eth:
                    eths[eth['name']] = (eth['mac'], 'dhcp', None, None)
                else:
                    brdev = get_network_bridge(self.conn, eth['network'])
                    addr = ifconfig.getAddr(brdev)
                    mask = ifconfig.getMask(brdev)
                    eths[eth['name']] = (eth['mac'], eth['ip'], netmask2netsz(mask), addr)

            if users is None:
                users = {vm.user: vm.passwd}

            try:
                prepare_guest(vm.image, vm.name, users, eths)
            except CloudError as x:
                print "Can't update vm image -", x

            logger.debug("Image ready - start vm {0}".format(vm.name))
            self.conn.createXML(tostring(vm_xm), 0)
            logger.debug("VM {0} started ok".format(vm.name))

    def stop_vm(self, vmname, timeout1=10, timeout2=2):

        logger.info("Stop vm/network {0}".format(vmname))

        vms = [vm for vm in self.vms.values()
                if vm.name == vmname or
                        vm.name.startswith(vmname +
                                           self.DOM_SEPARATOR)]

        vm_names = " ".join(vm.name for vm in vms)
        logger.debug("Found next vm's, which match name glob {0}".format(vm_names))

        for xvm in vms:
            logger.debug("Stop vm {0}".format(xvm.name))
            try:
                vm = self.conn.lookupByName(xvm.name)
            except libvirt.libvirtError:
                logger.debug("vm {0} don't exists - skip it".format(xvm.name))
            else:
                logger.debug("Shutdown vm {0}".format(xvm.name))
                vm.shutdown()

                for i in range(timeout1):
                    time.sleep(1)
                    try:
                        vm = self.conn.lookupByName(xvm.name)
                    except libvirt.libvirtError:
                        return

                logger.warning("VM {0} don't shoutdowned - destroy it".format(xvm.name))
                vm.destroy()
                for i in range(timeout2):
                    time.sleep(1)
                    try:
                        vm = self.conn.lookupByName(xvm.name)
                    except libvirt.libvirtError:
                        return

                logger.error("Can't stop vm {0}".format(xvm.name))
                raise CloudError("Can't stop vm {0}".format(xvm.name))

    def list_vms(self):
        for domain_id in self.conn.listDomainsID():
            yield self.conn.lookupByID(domain_id)

    def login_to_vm(self, name, users=None):
        vm = self.vms[name]
        ipaddr = get_vm_ssh_ip(self.conn, name)
        if ipaddr is None:
            raise RuntimeError("No one interface of {0} accepts ssh connection".format(name))
        else:
            if users is not None:
                login_ssh(ipaddr, users.keys()[0], users.values()[0])
            else:
                login_ssh(ipaddr, vm.user, vm.passwd)
