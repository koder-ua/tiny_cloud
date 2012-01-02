import re
import subprocess

from tiny_cloud.utils import parse_credentials, int2ip, ip2int, netmask2netsz, netsz2netmask
from tiny_cloud.network import ifconfig, ping, is_host_alive
from oktest import ok


def test_utils():
    ok(parse_credentials("user:passwd@domain")) == ('user', 'passwd', 'domain', None)
    ok(parse_credentials("user:passwd@domain+22")) == ('user', 'passwd', 'domain', 22)
    ok(parse_credentials("user:psdd:dd@domain+22")) == ('user', 'psdd:dd', 'domain', 22)
    ok(parse_credentials("user:pas@swd@domain+22")) == ('user', 'pas@swd', 'domain', 22)
    ok(parse_credentials("user:pas:@:@swd@domain+22")) == ('user', 'pas:@:@swd', 'domain', 22)

    ok(ip2int('127.0.0.1')) == 127 * 256 ** 3 + 1
    ok(ip2int('1.1.1.1')) == 256 ** 3 + 256 ** 2 + 256 + 1

    ok(int2ip(ip2int('127.0.0.1'))) == '127.0.0.1'
    ok(int2ip(ip2int('1.1.1.1'))) == '1.1.1.1'

    ok(netmask2netsz('0.0.0.0')) == 0
    ok(netmask2netsz('255.0.0.0')) == 8
    ok(netmask2netsz('255.255.0.0')) == 16
    ok(netmask2netsz('255.255.255.0')) == 24
    ok(netmask2netsz('255.255.255.255')) == 32
    ok(netmask2netsz('255.255.255.254')) == 31
    ok(netmask2netsz('255.255.255.240')) == 28

    ok(netsz2netmask(netmask2netsz('255.255.255.255'))) == '255.255.255.255'
    ok(netsz2netmask(netmask2netsz('255.255.255.254'))) == '255.255.255.254'
    ok(netsz2netmask(netmask2netsz('255.255.255.240'))) == '255.255.255.240'
    ok(netsz2netmask(netmask2netsz('255.255.255.0'))) == '255.255.255.0'
    ok(netsz2netmask(netmask2netsz('255.255.0.0'))) == '255.255.0.0'
    ok(netsz2netmask(netmask2netsz('255.0.0.0'))) == '255.0.0.0'
    ok(netsz2netmask(netmask2netsz('0.0.0.0'))) == '0.0.0.0'


def test_ifconfig():
    addr = subprocess.check_output('ip addr', shell=True)

    names = []
    addrs = {}
    up = {}
    cname = None

    for ln in addr.split('\n'):
        rr = re.match(r"\d+:\s+(?P<name>.*?): <", ln)
        if rr:
            cname = rr.group('name')
            names.append(cname)

            if re.search(r'\WUP\W', ln):
                up[cname] = True
            else:
                up[cname] = False

        rr = re.match(r"\s+inet\s+(?P<ip>[\d.]+)/(?P<sz>\d+)", ln)
        if rr:
            addrs[cname] = (rr.group('ip'), int(rr.group('sz')))

    ifnames = ifconfig.getInterfaceList()
    ifnames.sort()
    names.sort()

    ok(ifnames) == names

    for name in names:
        ok(ifconfig.getAddr(name)) == addrs[name][0]
        ok(netmask2netsz(ifconfig.getMask(name))) == addrs[name][1]
        ok(ifconfig.isUp(name)) == up[name]


def test_ping():
    for iname in  ifconfig.getInterfaceList():
        ip = ifconfig.getAddr(iname)
        ok(ping(ip, 0.1)) <= 0.1
        ok(is_host_alive(ip, 0.1)) == True












