from sockio.sio import DefaultEventLoop
from serialio.aio import serial_for_url as aio_serial_for_url


def async_to_sync(class_or_func, *args, **kwargs):
    resolve_futures = kwargs.pop("resolve_futures", True)
    async def create():
        return class_or_func(*args, **kwargs)

    serial = DefaultEventLoop.run_coroutine(create()).result()
    return DefaultEventLoop.proxy(serial, resolve_futures)


def serial_for_url(url, *args, **kwargs):
    return async_to_sync(aio_serial_for_url, *args, **kwargs)
