import urllib.parse


def serial_for_url(url, *args, **kwargs):
    addr = urllib.parse.urlparse(url)
    scheme = addr.scheme
    if scheme == "serial-tcp":
        import sockio.sio

        return sockio.sio.TCP(addr.hostname, addr.port, *args, **kwargs)
    raise ValueError("unsupported sync scheme {!r} for {}".format(scheme, url))
