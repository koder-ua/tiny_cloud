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

from __future__ import print_function

import termios, re, os, sys, tty
import time, array, struct, random
import fcntl, select, socket, logging, threading
import subprocess, platform

from xml.etree.ElementTree import fromstring

import paramiko
try:
    logging.getLogger("scapy.runtime").setLevel(logging.ERROR)
    from scapy.all import srp, Ether, ARP, conf
    conf.verb = 0
except ImportError:
    srp = None

from utils import netmask2netsz

logging.getLogger('ssh.transport').setLevel(logging.ERROR)

arch = platform.architecture()[0]
if arch == '32bit':
    IFNAMSIZ = 32
    ifreq_size = 32
elif arch == '64bit':
    IFNAMSIZ = 16
    ifreq_size = 40
else:
    raise OSError("Unknown architecture: %s" % arch)


class IfConfig(object):
    """Access to socket interfaces"""

    SIOCGIFNAME = 0x8910
    SIOCGIFCONF = 0x8912
    SIOCGIFFLAGS = 0x8913
    SIOCGIFADDR = 0x8915
    SIOCGIFBRDADDR = 0x8919
    SIOCGIFNETMASK = 0x891b
    SIOCGIFCOUNT = 0x8938

    IFF_UP = 0x1                # Interface is up.
    IFF_BROADCAST = 0x2         # Broadcast address valid.
    IFF_DEBUG = 0x4             # Turn on debugging.
    IFF_LOOPBACK = 0x8          # Is a loopback net.
    IFF_POINTOPOINT = 0x10      # Interface is point-to-point link.
    IFF_NOTRAILERS = 0x20       # Avoid use of trailers.
    IFF_RUNNING = 0x40          # Resources allocated.
    IFF_NOARP = 0x80            # No address resolution protocol.
    IFF_PROMISC = 0x100         # Receive all packets.
    IFF_ALLMULTI = 0x200        # Receive all multicast packets.
    IFF_MASTER = 0x400          # Master of a load balancer.
    IFF_SLAVE = 0x800           # Slave of a load balancer.
    IFF_MULTICAST = 0x1000      # Supports multicast.
    IFF_PORTSEL = 0x2000        # Can set media type.
    IFF_AUTOMEDIA = 0x4000      # Auto media select active.

    def __init__(self):
        # create a socket so we have a handle to query
        self.sockfd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def __del__(self):
        self.close()

    def close(self):
        if self.sockfd is not None:
            sock = self.sockfd
            self.sockfd = None
            sock.close()

    def _fcntl(self, func, args):
        return fcntl.ioctl(self.sockfd.fileno(), func, args)

    def _getaddr(self, ifname, func):
        ifreq = ifname.ljust(IFNAMSIZ + ifreq_size, '\x00')
        try:
            result = self._fcntl(func, ifreq)
        except IOError:
            return None

        return socket.inet_ntoa(result[20:24])

    MAXBYTES = 8 * 1024

    def getInterfaceList(self):
        """ Get all interface names in a list"""

        buff = array.array('B', '\0' * self.MAXBYTES)
        ptr, sz = buff.buffer_info()
        ifconf = struct.pack("iP", sz, ptr)

        fcntl_res = self._fcntl(self.SIOCGIFCONF, ifconf)

        # loop over interface names
        iflist = []
        sz = struct.calcsize('i')
        size = struct.unpack("i", fcntl_res[:sz])[0]
        buffstr = buff.tostring()

        for idx in range(0, size, ifreq_size):
            name = buffstr[idx:idx + IFNAMSIZ].split('\0', 1)[0]
            iflist.append(name)

        return iflist

    def getFlags(self, ifname):
        """ Get the flags for an interface
        """
        ifreq = (ifname + '\0' * 32)[:32]
        try:
            result = self._fcntl(self.SIOCGIFFLAGS, ifreq)
        except IOError:
            return 0

        # extract the interface's flags from the return value
        flags, = struct.unpack('H', result[16:18])

        # return "UP" bit
        return flags

    def getAddr(self, ifname):
        """ Get the inet addr for an interface
        """
        return self._getaddr(ifname, self.SIOCGIFADDR)

    def getMask(self, ifname):
        """ Get the netmask for an interface
        """
        return self._getaddr(ifname, self.SIOCGIFNETMASK)

    def getBroadcast(self, ifname):
        """ Get the broadcast addr for an interface
        """
        return self._getaddr(ifname, self.SIOCGIFBRDADDR)

    def isUp(self, ifname):
        """ Check whether interface 'ifname' is UP
        """
        return (self.getFlags(ifname) & self.IFF_UP) != 0


ifconfig = IfConfig()


class MacGenerator(object):

    MAX_3B_NUM = 256 ** 3

    def __init__(self, mac_template='00:44:01:{0:02X}:{1:02X}:{2:02X}'):
        self.mac_template = mac_template
        self.now = random.randint(0, self.MAX_3B_NUM - 1)
        self.mac_lock = threading.Lock()

    def get_next_mac(self):
        while True:
            with self.mac_lock:
                self.now = (self.now + 1) % self.MAX_3B_NUM
                mnow = self.now
            yield self.mac_template.format((mnow & 0xFF0000) >> 16,
                        (mnow & 0xFF00) >> 8,
                        mnow & 0xFF)

mg = MacGenerator()

ip_hwaddr_re = re.compile('(?P<ip>(?:\d{1,3}\.){3}\d{1,3})\s+(?P<hw>(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})')


def netscan_arpscan(dev):
    proc = subprocess.Popen(
            'arp-scan -I {0} -l'.format(dev).split(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)

    proc.wait()
    for i in proc.stdout.read().split('\n'):
        ip_hw_match = ip_hwaddr_re.match(i)
        if ip_hw_match:
            yield ip_hw_match.group('hw').upper(), ip_hw_match.group('ip')


def netscan_dnsmasq(lease_file=None):
    if lease_file is None:
        lease_file = "/var/lib/misc/dnsmasq.leases"

    with open(lease_file) as fd:
        for line in fd:
            _, mac, ip = line.split(' ', 3)[:3]
            yield mac.upper(), ip

if srp is not None:
    def netscan_scapy(dev):
        network = ifconfig.getAddr(dev)
        netmask = ifconfig.getMask(dev)
        netsize = netmask2netsz(netmask)

        ans, unans = srp(
            Ether(dst="ff:ff:ff:ff:ff:ff") / \
                ARP(pdst="{0}/{1}".format(network, netsize)),
                    timeout=0.1, iface=dev)

        for request, responce in ans:
            yield responce.payload.fields['hwsrc'].upper(), responce.payload.fields['psrc']
else:
    netscan_scapy = None


def netscan(dev, method="auto", lease_file=None):
    if method == 'auto' or method == 'scapy':
        if netscan_scapy is not None:
            return netscan_scapy(dev)
    if method == 'auto' or method == 'arp-scan':
        return netscan_arpscan(dev)

    if method == 'auto' or method == 'dnsmasq':
        return netscan_dnsmasq(lease_file=lease_file)

    raise ValueError("Can't found appropriate method for get ip addr")


def hw2ip(hw, dev, method="auto", lease_file=None):
    for fhw, ip in netscan(dev, method="auto", lease_file=None):
        if fhw == hw:
            return ip
    raise RuntimeError("Can't found ip address for {0!r}".format(hw))


def is_ssh_ready(ip, port=22):
    return is_port_open(ip, port)


def is_port_open(ip, port):
    s = socket.socket()
    s.settimeout(0.1)
    try:
        s.connect((ip, port))
        return True
    except socket.error:
        return False


def is_vm_online(conn, vmname):
    for ip in get_vm_ips(conn, vmname):
        if is_host_alive(ip, 0.1):
            return True
    return False


def get_network_bridge(conn, netname, br_map={}, clear=False):
    netid = (conn.getURI(), netname)
    if clear:
        br_map.clear()

    try:
        return br_map[netid]
    except KeyError:
        net = conn.networkLookupByName(netname)
        xml = fromstring(net.XMLDesc(0))
        br_name = xml.find('bridge').attrib['name']
        br_map[netid] = br_name
        return br_name


def get_vm_ips(conn, vmname):
    vm = conn.lookupByName(vmname)
    xml = vm.XMLDesc(0)
    xml_desc = fromstring(xml)

    for xml_iface in xml_desc.findall("devices/interface"):
        netname = xml_iface.find('source').attrib['network']
        lookup_hwaddr = xml_iface.find('mac').attrib['address']
        br_name = get_network_bridge(conn, netname)

        try:
            yield hw2ip(lookup_hwaddr, br_name)
        except RuntimeError:
            pass


def get_vm_ssh_ip(conn, vmname):
    for ip in get_vm_ips(conn, vmname):
        if is_ssh_ready(ip):
            return ip
    return None


def get_myaddress(iface="eth0"):
    """Return my primary IP address."""
    return ifconfig.getAddr(iface)


ICMP_ECHO_REQUEST = 8


def checksum(source_string):
    """
    I'm not too confident that this is right but testing seems
    to suggest that it gives the same answers as in_cksum in ping.c
    """
    csum = 0
    countTo = (len(source_string) / 2) * 2
    count = 0
    while count < countTo:
        thisVal = ord(source_string[count + 1]) * 256 + ord(source_string[count])
        csum = csum + thisVal
        csum = csum & 0xffffffff
        count = count + 2

    if countTo < len(source_string):
        csum = csum + ord(source_string[len(source_string) - 1])
        csum = csum & 0xffffffff

    csum = (csum >> 16) + (csum & 0xffff)
    csum = csum + (csum >> 16)
    answer = ~csum
    answer = answer & 0xffff

    # Swap bytes. Bugger me if I know why.
    answer = answer >> 8 | (answer << 8 & 0xff00)

    return answer


def receive_one_ping(my_socket, ID, timeout):
    """
    receive the ping from the socket.
    """
    timeLeft = timeout
    while True:
        startedSelect = time.time()
        whatReady = select.select([my_socket], [], [], timeLeft)
        howLongInSelect = (time.time() - startedSelect)
        if whatReady[0] == []:
            return

        timeReceived = time.time()
        recPacket, addr = my_socket.recvfrom(1024)
        icmpHeader = recPacket[20:28]
        type, code, checksum, packetID, sequence = struct.unpack(
            "bbHHh", icmpHeader
        )
        if packetID == ID:
            bytesInDouble = struct.calcsize("d")
            timeSent = struct.unpack("d", recPacket[28:28 + bytesInDouble])[0]
            return timeReceived - timeSent

        timeLeft = timeLeft - howLongInSelect
        if timeLeft <= 0:
            return


def send_one_ping(my_socket, dest_addr, ID):
    """
    Send one ping to the given >dest_addr<.
    """
    dest_addr = socket.gethostbyname(dest_addr)

    # Header is type (8), code (8), checksum (16), id (16), sequence (16)
    my_checksum = 0

    # Make a dummy heder with a 0 checksum.
    header = struct.pack("bbHHh", ICMP_ECHO_REQUEST, 0, my_checksum, ID, 1)
    bytesInDouble = struct.calcsize("d")
    data = (192 - bytesInDouble) * "Q"
    data = struct.pack("d", time.time()) + data

    # Calculate the checksum on the data and the dummy header.
    my_checksum = checksum(header + data)

    # Now that we have the right checksum, we put that in. It's just easier
    # to make up a new header than to stuff it into the dummy.
    header = struct.pack(
        "bbHHh", ICMP_ECHO_REQUEST, 0, socket.htons(my_checksum), ID, 1
    )
    packet = header + data
    my_socket.sendto(packet, (dest_addr, 1))


def ping(dest_addr, timeout=1):
    """
    Returns either the delay (in seconds) or none on timeout.
    """
    icmp = socket.getprotobyname("icmp")
    try:
        my_socket = socket.socket(socket.AF_INET, socket.SOCK_RAW, icmp)
    except socket.error, (errno, msg):
        if errno == 1:
            # Operation not permitted
            msg = msg + (
                " - Note that ICMP messages can only be sent from processes"
                " running as root."
            )
            raise socket.error(msg)
        raise

    my_ID = os.getpid() & 0xFFFF

    send_one_ping(my_socket, dest_addr, my_ID)
    delay = receive_one_ping(my_socket, my_ID, timeout)

    my_socket.close()
    return delay


def is_host_alive(ip, timeout=1, method='internal'):
    if method == 'internal':
        return ping(ip, timeout) is not None
    return os.system('ping -c 1 -W 1 {0} 2>&1 > /dev/null'.format(ip)) == 0


def login_ssh(ip, user, passwd, port=22, timeout=1, method='paramiko'):
    if method == 'paramiko':
        login_ssh_paramiko(ip, user, passwd, port=port, timeout=timeout)
    else:
        login_ssh_expect(ip, user, passwd, port=port)


def posix_shell(chan):
    oldtty = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin.fileno())
        tty.setcbreak(sys.stdin.fileno())
        chan.settimeout(0.0)

        while True:
            r, w, e = select.select([chan, sys.stdin], [], [])
            if chan in r:
                try:
                    x = chan.recv(1024)
                    if len(x) == 0:
                        break
                    sys.stdout.write(x)
                    sys.stdout.flush()
                except socket.timeout:
                    pass

            if sys.stdin in r:
                x = sys.stdin.read(1)
                if len(x) == 0:
                    break
                chan.send(x)

    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, oldtty)


def login_ssh_paramiko(ip, user, passwd, port=22, timeout=1):
    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(ip, port=int(port), username=user, password=passwd,
                      allow_agent=False, timeout=timeout)
    chan = ssh.invoke_shell()
    posix_shell(chan)
    chan.close()


expect_login = """expect -c'
spawn ssh -p {port} {user}@{ip};
while {{1}} {{
  expect {{
    eof                          {{ break }};
    "The authenticity of host"   {{ send "yes\\n" }};
    "password:"                  {{ send "{passwd}\\n"; interact; break;}};
  }};
}};
wait'
"""


def login_ssh_expect(ip, user, passwd, port=22):
    os.system(expect_login.format(user=user, ip=ip, passwd=passwd, port=port))
