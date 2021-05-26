from serialio.sio import async_to_sync
from serialio.aio.posix import Serial as AIOSerialPosix


def Serial(*args, **kwargs):
    return async_to_sync(AIOSerialPosix, *args, **kwargs)

