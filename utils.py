import re

cred_rr = r"(?P<login>.*?):(?P<passwd>.*)@(?P<host>[^+]*)(?P<port>\+\d+)?"


def parse_credentials(credentials):
    rr = re.match(cred_rr, credentials)
    if rr is None:
        raise ValueError("Can't parse credentials string {0!r}".format(credentials))

    if rr.group('port') is None:
        port = None
    else:
        port = int(rr.group('port'))

    return rr.group('login'), rr.group('passwd'), rr.group('host'), port


def ip2int(ip):
    it = zip(map(int, ip.split('.')),
             (256 ** x for x in range(4)[::-1]))
    return sum(x * y for x, y in it)


def int2ip(val):
    vals = [((val & (0xFF << (step * 8))) >> (step * 8)) for step in range(4)][::-1]
    return ".".join(map(str, vals))


def netmask2netsz(netmask):
    nv = ip2int(netmask)
    for pos in range(33):
        if ((nv >> pos) % 2 != 0):
            break
    return 32 - pos


def netsz2netmask(netsz):
    res = 0
    for pos in range(netsz):
        res = res | 1 << (31 - pos)
    return int2ip(res)
