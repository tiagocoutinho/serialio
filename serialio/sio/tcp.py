from serialio.sio import async_to_sync
from serialio.aio.tcp import Serial as AIOSerialTCP


def Serial(*args, **kwargs):
    return async_to_sync(AIOSerialTCP, *args, **kwargs)
