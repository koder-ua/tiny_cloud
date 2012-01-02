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
import uuid
import glob
import crypt
import random
import subprocess
import contextlib

try:
    import guestfs
    no_guestfs = False
except ImportError:
    no_guestfs = True

from utils import netsz2netmask, ip2int, int2ip, logger
from common import CloudError


def run(cmd):
    return subprocess.check_output(cmd, shell=True)


@contextlib.contextmanager
def make_image(src_fname,
               tempo_files_dir,
               dst_format,
               qcow2_compress=False,
               qcow2_preallocate=False,
               lvm_dev1=None,
               lvm_dev2=None,
               delete_on_exit=True):

    bstore_raw = None
    is_bstore_dev = False

    if dst_format == 'qcow2_on_lvm':
        bstore_raw = lvm_dev2
        is_bstore_dev = True
        dst_fname = os.path.join(tempo_files_dir, str(uuid.uuid1()))
        dst_format = 'qcow2_on_raw'
        is_dst_dev = True
        rm_files = [dst_fname]
    elif dst_format == 'qcow2_on_raw':
        bstore_raw = os.path.join(tempo_files_dir, str(uuid.uuid1()))
        dst_fname = os.path.join(tempo_files_dir, str(uuid.uuid1()))
        dst_format = 'qcow2_on_raw'
        is_dst_dev = True
        rm_files = [dst_fname, bstore_raw]
    elif dst_format == 'lvm':
        dst_format = 'raw'
        dst_fname = lvm_dev1
        is_dst_dev = True
    else:
        dst_fname = os.path.join(tempo_files_dir, str(uuid.uuid1()))
        rm_files = [dst_fname]
        is_dst_dev = False

    convert = lambda cmd: \
                    run("qemu-img convert -f qcow2 -O {0} {1} {2}".format(\
                                cmd, src_fname, dst_fname))

    opts = ""

    if qcow2_preallocate:
        assert dst_format in ('qcow2', 'qcow2_on_raw', 'qcow2_on_qcow2')
        opts = " -o preallocation=metadata "

    if qcow2_compress:
        assert dst_format in ('qcow2', 'qcow2_on_raw', 'qcow2_on_qcow2')
        opts = opts + " -c "

    if dst_format == 'qcow2':
        run('cp {0} {1}'.format(src_fname, dst_fname))
    elif dst_format == 'qcow':
        convert('qcow')
    elif dst_format == 'raw':
        if is_dst_dev:
            convert("host_device")
        else:
            convert("raw")
    elif dst_format == 'qcow2_on_qcow2':
        run("qemu-img create {0} -f qcow2 -o backing_file={1} {2}".format(opts, src_fname, dst_fname))
    elif dst_format == 'qcow2_on_raw':

        if is_bstore_dev:
            frmt = 'host_device'
        else:
            frmt = 'raw'

        run("qemu-img convert -f qcow2 -O {0} {1} {2}".format(frmt, src_fname, bstore_raw))
        run("qemu-img create {0} -f qcow2 -o backing_fmt=raw,backing_file={1} {2}".format(
                            opts, bstore_raw, dst_fname))

    else:
        raise RuntimeError("Unknown storage type %r" % (dst_format,))

    try:
        yield dst_fname
    finally:
        if delete_on_exit:
            map(os.unlink, rm_files)


class LocalGuestFS(object):
    def __init__(self, root):
        self.root = root

    def path(self, rpath):
        return os.path.join(self.root, rpath[1:])

    def write(self, fname, val):
        open(self.path(fname), 'w').write(val)

    def read_file(self, fname):
        return open(self.path(fname), 'r').read()

    def exists(self, path):
        return os.path.exists(self.path(path))

    def mkdir_p(self, path):
        cp = self.root
        path_els = path.split(path[1:], '/')
        for el in path_els:
            cp = os.path.join(cp, el)
            os.mkdir(cp)


def prepare_guest(*dt, **mp):
    if no_guestfs:
        raise CloudError("No libguestfs found. Can't manage vm images")
    return prepare_guest_debian(*dt, **mp)


@contextlib.contextmanager
def mount_dimage(image, mdir, dev='/dev/nbd0'):
    cmd = "qemu-nbd -c {dev} {img}".format(dev=dev, image=image)
    subprocess.check_call(cmd, shell=True)

    try:
        for pdev in glob.glob(dev + 'p*'):
            subprocess.check_call('mount {dev} {mdir}'.format(dev=dev, mdir=mdir))
            if os.path.exists(os.path.isdir(mdir, 'etc')):
                break
            subprocess.check_call("umount " + pdev)

        if not os.path.exists(os.path.isdir(mdir, 'etc')):
            raise CloudError("Can't found root partition in file " + image)

        try:
            yield LocalGuestFS(mdir)
        finally:
            subprocess.call('umount ' + pdev)
    finally:
        subprocess.check_call("qemu-nbd -d " + dev, shell=True)


# eth_devs => {'eth0' : (hw, ip/'dhcp', sz/None, gw/None)}
# passwords => {login:passwd}
def prepare_guest_debian(disk_path, hostname, passwords, eth_devs, format=None, apt_proxy_ip=None):

    logger.info("Prepare image for " + hostname)
    if format == 'lxc':
        gfs = LocalGuestFS(disk_path)
    else:
        gfs = guestfs.GuestFS()
        gfs.add_drive_opts(disk_path, format=format)
        logger.debug("Launch libguestfs vm")
        gfs.launch()
        logger.debug("ok")

        #print gfs.list_partitions()
        for dev, fs_type in  gfs.list_filesystems():
            logger.debug("Fount partition {0} with fs type {1}".format(dev, fs_type))
            if fs_type in 'ext2 ext3 reiserfs xfs jfs btrfs':
                gfs.mount(dev, '/')
                if gfs.exists('/etc'):
                    logger.debug("Fount /etc on partition {0} - will work on it".format(dev))
                    break
                gfs.umount(dev)
                logger.debug("No /etc dir found - continue")

        if not gfs.exists('/etc'):
            msg = "Can't fount /etc dir in image " + disk_path
            logger.error(msg)
            raise CloudError(msg)

    logger.debug("Launch ok. Set hostname")
    #hostname
    gfs.write('/etc/hostname', hostname)

    #set device names
    logger.debug("Set device names and network imterfaces")
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
            interfaces.append("    address " + ip)
            network = int2ip(ip2int(ip) & ip2int(netsz2netmask(sz)))
            interfaces.append("    network " + network)
            interfaces.append("    netmask " + netsz2netmask(sz))

    gfs.write('/etc/udev/rules.d/70-persistent-net.rules', "\n".join(rules_fc))
    gfs.write('/etc/network/interfaces', "\n".join(interfaces))

    # update passwords
    logger.debug("Update passwords")

    chars = "".join(chr(i) for i in range(ord('a'), ord('z') + 1))
    chars += "".join(chr(i) for i in range(ord('A'), ord('Z') + 1))
    chars += "".join(chr(i) for i in range(ord('0'), ord('9') + 1))

    hashes = {}
    for login, passwd in passwords.items():
        salt = "".join(random.choice(chars) for _ in range(8))
        hashes[login] = crypt.crypt(passwd, "$6$" + salt)

    new_shadow = []
    need_logins = set(hashes)

    for ln in gfs.read_file('/etc/shadow').split('\n'):
        ln = ln.strip()
        if ln != '' and ln[0] != '#':
            login = ln.split(':', 1)[0]
            if login in hashes:
                new_shadow.append("{login}:{hash}:{rest}".format(login=login,
                                                                   hash=hashes[login],
                                                                   rest=ln.split(':', 2)[2]))
                need_logins.remove(login)
        else:
            new_shadow.append(ln)

    for login in need_logins:
        new_shadow.append("{login}:{hash}:{rest}".format(login=login,
                                                         hash=hashes[login],
                                                         rest="0:0:99999:7:::"))

    gfs.write('/etc/shadow', "\n".join(new_shadow))

    # add new users to passwd
    ids = []
    logins = []
    passwd = gfs.read_file('/etc/passwd')
    for ln in passwd.split('\n'):
        ln = ln.strip()
        if ln != '' and ln[0] != '#':
            logins.append(ln.split(':', 1)[0])
            ids.append(ln.split(':')[2])
            ids.append(ln.split(':')[3])

    add_lines = []
    try:
        mid = max(i for i in ids if i < 65000)
    except ValueError:
        mid = 0
    mid += 1024

    for login in set(hashes) - set(logins):
        home = '/home/' + login
        add_lines.append(":".join([login, 'x', str(mid), str(mid), "", home, '/bin/bash']))
        if not gfs.exists(home):
            gfs.mkdir_p(home)
        mid += 1

    if add_lines != []:
        gfs.write('/etc/passwd', passwd.rstrip() + "\n" + "\n".join(add_lines))

    if apt_proxy_ip is not None:
        logger.debug("Set apt-proxy to http://{0}:3142".format(apt_proxy_ip))
        fc = 'Acquire::http {{ Proxy "http://{0}:3142"; }};'.format(apt_proxy_ip)
        gfs.write('/etc/apt/apt.conf.d/02proxy', fc)

    logger.debug("Update hosts")

    hosts = gfs.read_file('/etc/hosts')

    new_hosts = ["127.0.0.1 localhost\n127.0.0.1 " + hostname]
    for ln in hosts.split('#'):
        if not ln.strip().startswith('127.0.0.1'):
            new_hosts.append(ln)

    gfs.write('/etc/hosts', "\n".join(new_hosts))

    #for fname in ('/etc/hostname', '/etc/passwd', '/etc/shadow', '/etc/network/interfaces', '/etc/apt/apt.conf.d/02proxy', '/etc/udev/rules.d/70-persistent-net.rules'):
    #    print '-' * 50
    #    print fname, "=>"
    #    print gfs.read_file(fname)

#src = '/home/koder/vm_images/debian_squeeze_amd64_standard.qcow2'
#with make_image(src, '/tmp', 'qcow2_on_qcow2') as fname:
#    prepare_guest(fname, 'deb_test', {'root': 'www', 'koder': 'mmm'}, {'eth0': ('00:00:00:00:00:00', '1.2.3.4', 24, '1.2.3.1'),
#                                                       'eth1': ('00:00:00:00:00:01', 'dhcp', None, None)},
#                        format='qcow2', apt_proxy_ip='1.2.3.1')
