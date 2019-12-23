import logging
import urllib.parse

import sockio.aio

from serial import SerialException

from ..base import SerialBase


log = logging.getLogger('serialio.tcp.aio')


class Serial(SerialBase):
    """Serial port implementation for plain tcp sockets."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        url = urllib.parse.urlparse(self._port)
        host, port = url.hostname, url.port
        self._socket = sockio.aio.TCP(host, port, eol=self._eol)

    @property
    def is_open(self):
        return self._socket.connected

    @is_open.setter
    def is_open(self, value):
        pass

    @property
    def in_waiting(self):
        return self._socket.in_waiting

    async def _reconfigure_port(self):
        raise SerialException('Cannot reconfigure raw TCP Serial connection')

    async def open(self):
        await self._socket.open()

    async def close(self):
        await self._socket.close()

    async def read(self, size=1):
        return await self._socket.read(size)

    async def readline(self, eol=None):
        return await self._socket.readline(eol=eol)

    async def readlines(self, n, eol=None):
        return await self._socket.readlines(n, eol=eol)

    async def readuntil(self, separator=b'\n'):
        return await self._socket.readuntil(separator)

    async def readbuffer(self):
        """Read all bytes currently available in the buffer of the OS"""
        return await self._socket.readbuffer()

    async def write(self, data):
        return await self._socket.write(data)

    async def writelines(self, lines):
        return await self._socket.writelines(lines)

    async def write_readline(self, data, eol=None):
        return await self._socket.write_readline(data, eol=eol)

    async def write_readlines(self, data, n, eol=None):
        return await self._socket.write_readlines(data, n, eol=eol)

    async def writelines_readlines(self, lines, n=None, eol=None):
        return await self._socket.writelines_readlines(lines, n=n, eol=eol)

    def reset_input_buffer(self):
        self._socket.reset_input_buffer()
