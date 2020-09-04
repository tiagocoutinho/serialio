import logging
import urllib.parse

import sockio.aio

from .base import LF, SerialBase, SerialException, assert_open, async_assert_open


log = logging.getLogger("serialio.tcp.aio")


class Serial(SerialBase):
    """Serial port implementation for plain tcp sockets."""

    def __init__(self, *args, **kwargs):
        self._socket = None
        self.logger = log
        super().__init__(*args, **kwargs)

    def _do_disconnect(self, *args):
        self.is_open = False

    @property
    @assert_open
    def in_waiting(self):
        return self._socket.in_waiting

    async def _reconfigure_port(self):
        if self._socket is None:
            raise serial.SerialException("Can only operate on open ports")

    async def open(self):
        if self._port is None:
            raise serial.SerialException(
                "Port must be configured before it can be used."
            )
        if self.is_open:
            raise serial.SerialException("Port is already open.")
        url = urllib.parse.urlparse(self._port)
        host, port = url.hostname, url.port
        self._socket = sockio.aio.TCP(
            host, port, eol=self._eol, timeout=self._timeout, auto_reconnect=False,
            on_eof_received=self._do_disconnect, on_connection_lost=self._do_disconnect
        )
        await self._socket.open()
        self.is_open = True

    async def close(self):
        if self._socket:
            await self._socket.close()
        self.is_open = False

    @async_assert_open
    async def read(self, size=1):
        return await self._socket.read(size)

    @async_assert_open
    async def readline(self, eol=None):
        return await self._socket.readline(eol=eol)

    @async_assert_open
    async def readuntil(self, separator=LF):
        return await self._socket.readuntil(separator)

    @async_assert_open
    async def read_all(self):
        return await self._socket.readbuffer()

    @async_assert_open
    async def write(self, data):
        return await self._socket.write(data)

    @async_assert_open
    async def reset_input_buffer(self):
        self._socket.reset_input_buffer()

    @async_assert_open
    async def reset_output_buffer(self):
        # ignored in raw tcp socket
        pass

    @async_assert_open
    async def send_break(self, duration=0.25):
        # ignored in raw tcp socket
        pass

    # Extra interface not provided by serial.Serial

    @async_assert_open
    async def readlines(self, n, eol=None):
        return await self._socket.readlines(n, eol=eol)

    @async_assert_open
    async def writelines(self, lines):
        return await self._socket.writelines(lines)

    @async_assert_open
    async def write_readline(self, data, eol=None):
        return await self._socket.write_readline(data, eol=eol)

    @async_assert_open
    async def write_readlines(self, data, n, eol=None):
        return await self._socket.write_readlines(data, n, eol=eol)

    @async_assert_open
    async def writelines_readlines(self, lines, n=None, eol=None):
        return await self._socket.writelines_readlines(lines, n=n, eol=eol)
