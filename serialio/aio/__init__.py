import urllib.parse


def serial_for_url(url, *args, **kwargs):
    addr = urllib.parse.urlparse(url)
    scheme = addr.scheme
    if scheme == "serial":
        # local serial line
        url = addr.path
        from .posix import Serial
    elif scheme in {"serial-rfc2217", "serial+rfc2217", "rfc2217"}:
        from .rfc2217 import Serial
    elif scheme in {"serial-tcp", "serial+tcp", "tcp"}:
        from .tcp import Serial
    elif scheme in {"serial-tango", "serial+tango", "tango"}:
        from .tango import Serial
    else:
        raise ValueError("unsupported async scheme {!r} for {}".format(scheme, url))
    return Serial(url, *args, **kwargs)
