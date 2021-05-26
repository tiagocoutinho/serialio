from serialio.sio import async_to_sync
from serialio.aio.tango import Serial as AIOSerialTango


def Serial(*args, **kwargs):
    return async_to_sync(AIOSerialTango, *args, **kwargs)
