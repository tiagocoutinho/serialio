__version__ = "2.2.1"


CONCURRENCY_MAP = {
    "sync": "sync",
    "syncio": "sync",
    "async": "async",
    "asyncio": "async",
}


def serial_for_url(url, *args, **kwargs):
    conc = kwargs.pop("concurrency", "async")
    concurrency = CONCURRENCY_MAP.get(conc)
    if concurrency == "async":
        from . import aio

        return aio.serial_for_url(url, *args, **kwargs)
    elif concurrency == "sync":
        from . import sio

        return sio.serial_for_url(url, *args, **kwargs)
    raise ValueError("unsupported concurrency {!r}".format(conc))
