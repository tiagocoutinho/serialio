from serialio.sio import async_to_sync
from serialio.aio.rfc2217 import Serial as AIOSerialRFC2217


def Serial(*args, **kwargs):
    return async_to_sync(AIOSerialRFC2217, *args, **kwargs)
