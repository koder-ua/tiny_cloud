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
import stat
import os.path
import subprocess
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
        mac_re = re.compile(':'.join([r"[\da-fA-F][\da-fA-F]"] * 6))
        ip_re = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}")
        network_re = re.compile(r"[a-zA-Z_][-\w_]*")

    def __init__(self, name, **keys):
        self.name = name
        self.mem = int(keys.pop('mem', 1024))
        self.htype = keys.pop('htype', 'kvm')
        self.vcpu = int(keys.pop('vcpu', 1))

        credentials = keys.pop('credentials', 'root:root')
        self.user, self.passwd = credentials.split(':')
        if 'image' in keys:
            self.images = [keys.pop('image')]
        else:
            self.images = keys.pop('images')
        self.opts = [i.strip() for i in keys.pop('opts', "").split()]

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
        self.url = data.get('url', 'qemu:///system')

        self.name = name
        self.ip = int2ip(ip2int(self.ip1) + 1)
        self.bridge = data['bridge'].strip()
        self.netmask = netsz2netmask(self.sz)


class TinyCloud(object):
    def_connection = 'qemu:///system'
    def __init__(self, vms, templates, networks,
                 urls, root, **defaults):

        self.urls = urls
        self.vms = {}
        self.templates = templates
        self.add_vms(vms)
        self.root = root
        self.networks = [Network(name, **data)
                         for name, data in networks.items()]
        msg = "Cloud with {0} vm templates created".format(self.vms.keys())
        logger.debug(msg)
        self.defaults = defaults

    DOM_SEPARATOR = '.'

    def add_vm(self, name, **params):
        self.vms[name] = VM(name, **params)

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

    def get_vm_conn(self, vmname):
        return libvirt.open(self.urls[self.vms[vmname].htype])

    def get_vm_ssh_ip(self, vmname):
        return get_vm_ssh_ip(self.get_vm_conn(vmname), vmname)

    def get_vm_ips(self, vmname):
        return get_vm_ips(self.get_vm_conn(vmname), vmname)

    def start_net(self, name):
        logger.info("Start network " + name)

        if name in self.networks:
            conn = libvirt.open(self.urls[self.networks[name].htype])
        else:
            conn = libvirt.open(self.def_connection)

        try:
            net = conn.networkLookupByName(name)
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
            conn.networkCreateXML(str(xml))

    def start_vm(self, vmname, users, prepare_image=False):
        logger.info("Start vm/network {0} with credentials {1}".format(vmname, users))

        vms = [vm for vm in self.vms.values()
                if vm.name == vmname or
                    vm.name.startswith(vmname +
                                        self.DOM_SEPARATOR)]
        vm_names = " ".join(vm.name for vm in vms)
        logger.debug("Found next vm's, which match name glob {0}".format(vm_names))

        for vm in vms:
            logger.debug("Prepare vm {0}".format(vm.name))

            path = os.path.join(self.root, self.templates[vm.htype])
            vm_xml_templ = open(path).read()
            logger.info("Use template '{0}'".format(path))

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

            disk_emulator = self.defaults.get('disk_emulator', 'qemu')

            if 'virtio' in vm.opts:
                bus = 'virtio'
            else:
                bus = 'ide'

            if 'ide' == bus:
                dev_name_templ = 'hd'
            elif 'scsi' == bus:
                dev_name_templ = 'sd'
            elif 'virtio' == bus:
                dev_name_templ = 'vd'

            letters = [chr(ord('a') + pos) for pos in range(ord('z') - ord('a'))]

            for hdd_pos, image in enumerate(vm.images):

                if hdd_pos > len(letters):
                    raise CloudError("To many HHD devices {0}".format(len(vm.images)))
                
                rimage = image

                dev_st = os.stat(rimage)
                while stat.S_ISLNK(dev_st.st_mode):
                    rimage = os.readlink(rimage)
                    dev_st = os.stat(rimage)

                dev = dev_name_templ + letters[hdd_pos]

                if stat.S_ISDIR(dev_st.st_mode):
                    hdd = xmlbuilder.XMLBuilder('filesystem', type='mount')
                    hdd.source(dir=image)
                    hdd.target(dir='/')
                else:
                    res = subprocess.check_output(['qemu-img', 'info', image])
                    hdr = "file format: "
                    tp = None
                    for line in res.split('\n'):
                        if line.startswith(hdr):
                            tp = line[len(hdr):].strip()
                    assert tp is not None

                    if stat.S_ISBLK(dev_st.st_mode):
                        hdd = xmlbuilder.XMLBuilder('disk', device='disk', type='block')
                        hdd.driver(name=disk_emulator, type=tp)
                        hdd.source(dev=image)
                        hdd.target(bus=bus, dev=dev)
                    elif stat.S_ISREG(dev_st.st_mode):
                        hdd = xmlbuilder.XMLBuilder('disk', device='disk', type='file')
                        hdd.driver(name=disk_emulator, type=tp)
                        hdd.source(file=image)
                        hdd.target(bus=bus, dev=dev)
                    else:
                        raise CloudError("Can't connect hdd device {0!r}".format(image))

                devs.append(~hdd)

            eths = {}

            conn = self.get_vm_conn(vm.name)

            for eth in vm.eths():
                edev = xmlbuilder.XMLBuilder('interface', type='network')
                edev.source(network=eth['network'])
                edev.mac(address=eth['mac'])
                devs.append(~edev)

                if 'ip' not in eth:
                    eths[eth['name']] = (eth['mac'], 'dhcp', None, None)
                else:
                    brdev = get_network_bridge(conn, eth['network'])
                    addr = ifconfig.getAddr(brdev)
                    mask = ifconfig.getMask(brdev)
                    eths[eth['name']] = (eth['mac'], eth['ip'], netmask2netsz(mask), addr)

            if users is None:
                users = {vm.user: vm.passwd}

            try:
                if vm.htype == 'lxc':
                    prepare_guest(vm.images[0], vm.name, users, eths, format='lxc')
                elif prepare_image:
                    prepare_guest(vm.images[0], vm.name, users, eths)
            except CloudError as x:
                print "Can't update vm image -", x

            logger.debug("Image ready - start vm {0}".format(vm.name))

            conn.createXML(tostring(vm_xm), 0)
            logger.debug("VM {0} started ok".format(vm.name))
            conn.close()

    def stop_vm(self, vmname, timeout1=10, timeout2=2):

        logger.info("Stop vm/network {0}".format(vmname))

        vms = [vm for vm in self.vms.values()
                if vm.name == vmname or
                   vm.name.startswith(vmname + self.DOM_SEPARATOR)]

        vm_names = " ".join(vm.name for vm in vms)
        logger.debug("Found next vm's, which match name glob {0}".format(vm_names))

        for xvm in vms:
            conn = self.get_vm_conn(xvm.name)
            logger.debug("Stop vm {0}".format(xvm.name))
            
            try:
                vm = conn.lookupByName(xvm.name)
            except libvirt.libvirtError:
                logger.debug("vm {0} don't exists - skip it".format(xvm.name))
            else:
                logger.debug("Shutdown vm {0}".format(xvm.name))
                
                try:
                    vm.shutdown()
                except libvirt.libvirtError:
                    pass
                else:
                    for i in range(timeout1):
                        time.sleep(1)
                        try:
                            vm = conn.lookupByName(xvm.name)
                        except libvirt.libvirtError:
                            return

                logger.warning("VM {0} don't shoutdowned - destroy it".format(xvm.name))
                vm.destroy()
                for i in range(timeout2):
                    time.sleep(1)
                    try:
                        vm = conn.lookupByName(xvm.name)
                    except libvirt.libvirtError:
                        return

                logger.error("Can't stop vm {0}".format(xvm.name))
                raise CloudError("Can't stop vm {0}".format(xvm.name))
            conn.close()

    def list_vms(self):
        for url in self.urls.values():
            conn = libvirt.open(url)
            for domain_id in conn.listDomainsID():
                yield conn.lookupByID(domain_id)
            conn.close()

    def login_to_vm(self, vmname, users=None):
        vm = self.vms[vmname]
        ipaddr = get_vm_ssh_ip(self.get_vm_conn(vmname), vmname)
        if ipaddr is None:
            raise CloudError("No one interface of {0} accepts ssh connection".format(vmname))
        else:
            if users is not None:
                login_ssh(ipaddr, users.keys()[0], users.values()[0])
            else:
                login_ssh(ipaddr, vm.user, vm.passwd)
