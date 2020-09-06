import urllib.parse


def serial_for_url(url, *args, **kwargs):
    addr = url
    url_result = urllib.parse.urlparse(url)
    scheme = url_result.scheme
    if scheme == "serial":
        # local serial line
        addr = url_result.path
        from .posix import Serial
    elif scheme == "rfc2217":
        from .rfc2217 import Serial
    elif scheme == "serial-tcp":
        from .tcp import Serial
    else:
        raise ValueError(
            "unsupported scheme {!r} for {}".format(scheme, url)
        )
    return Serial(addr, *args, **kwargs)
