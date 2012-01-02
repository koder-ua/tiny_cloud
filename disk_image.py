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

import os
import crypt
import random

import guestfs

from utils import netsz2netmask, ip2int, int2ip


class LocalGuestFS(object):
    def __init__(self, root):
        self.root = root

    def write(self, fname, val):
        open(os.path.join(self.root, fname), 'w').write(val)

    def read_file(self, path):
        return open(os.path.join(self.root, path), 'r').read()


def prepare_host(*dt, **mp):
    return prepare_guest_debian(*dt, **mp)


# eth_devs => {'eth0' : (hw, ip/'dhcp', sz/None, gw/None)}
# passwords => {login:passwd}
def prepare_guest_debian(disk_path, hostname, passwords, eth_devs, format=None, apt_proxy_ip=None):

    if format == 'lxc':
        gfs = LocalGuestFS(disk_path)
    else:
        gfs = guestfs.GuestFS()
        gfs.add_drive_opts(disk_path, format=format)
        gfs.launch()

        #print g.list_partitions()
        #print g.list_filesystems()
        #return

        gfs.mount('/dev/nova/root', '/')

    #hostname
    gfs.write('/etc/hostname', hostname)

    #set device names
    templ = 'SUBSYSTEM=="net", DRIVERS=="?*", ATTR{{address}}=="{hw}", NAME="{name}"'
    rules_fc = []
    interfaces = ["auto lo\niface lo inet loopback"]

    for dev, (hw, ip, sz, gw) in eth_devs.items():
        rules_fc.append(templ.format(hw=hw, name=dev))
        interfaces.append("auto " + dev)

        if ip == 'dhcp':
            interfaces.append("iface {0} inet dhcp".format(dev))
        else:
            interfaces.append("iface {0} inet static".format(dev))
            interfaces.append("address " + ip)
            network = int2ip(ip2int(ip) & ip2int(netsz2netmask(sz)))
            interfaces.append("network " + network)
            interfaces.append("netmask " + netsz2netmask(sz))

    gfs.write('/etc/udev/rules.d/70-persistent-net.rules', "\n".join(rules_fc))
    gfs.write('/etc/network/interfaces', "\n".join(interfaces))

    # update passwords
    chars = "".join(chr(i) for i in range(ord('a'), ord('z') + 1))
    chars += "".join(chr(i) for i in range(ord('A'), ord('Z') + 1))
    chars += "".join(chr(i) for i in range(ord('0'), ord('9') + 1))

    hashes = {}
    for login, passwd in passwords.items():
        salt = "".join(random.choice(chars) for _ in range(8))
        hashes[login] = crypt.crypt(passwd, "$6$" + salt)

    new_shadow = []
    for ln in gfs.read('/etc/shadow').split('\n'):
        login = ln.split(':', 1)[0]
        if login in hashes:
            new_shadow.append("{login}:{passwd}:{rest}".format(login=login,
                                                               hash=hashes[login],
                                                               rest=ln.split(':', 2)[2]))
        else:
            new_shadow.append(ln)

    gfs.write('/etc/shadow', "\n".join(new_shadow))

    # add new users to passwd
    ids = []
    logins = []
    passwd = gfs.read('/etc/passwd')
    for ln in passwd.split('\n'):
        logins.append(ln.split(':', 1)[0])
        ids.append(ln.split(':')[2])
        ids.append(ln.split(':')[3])

    add_lines = []
    mid = max(i for i in ids if i < 65000) + 1024
    for login in set(hashes) - set(logins):
        add_lines.append(":".join([login, 'x', str(mid), str(mid), "", '/home/' + login, '/bin/bash']))
        mid += 1

    if add_lines != []:
        gfs.write(passwd.rstrip() + "\n" + "\n".join(add_lines))

    if apt_proxy_ip is not None:
        fc = 'Acquire::http {{ Proxy "http://{0}:3142"; }};'.format(apt_proxy_ip)
        gfs.write('/etc/apt/apt.conf.d/02proxy', fc)
