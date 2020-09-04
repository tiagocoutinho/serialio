import logging
import functools
import urllib.parse

import serial
import sockio.aio


log = logging.getLogger("serialio.tcp.aio")


def assert_open(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.is_open:
            raise serial.portNotOpenError
        return func(self, *args, **kwargs)

    return wrapper


class Serial(serial.SerialBase):
    """Serial port implementation for plain tcp sockets."""

    def __init__(self, port=None, **kwargs):
        self._socket = None
        self._eol = kwargs.pop("eol", serial.LF)
        super().__init__(**kwargs)
        self.port = port

    @property
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
        self._socket = sockio.aio.TCP(host, port, eol=self._eol, timeout=self._timeout)
        self.is_open = True

    async def close(self):
        if self.is_open:
            if self._socket:
                await self._socket.close()
            self.is_open = False

    @assert_open
    async def read(self, size=1):
        return await self._socket.read(size)

    @assert_open
    async def readline(self, eol=None):
        return await self._socket.readline(eol=eol)

    @assert_open
    async def read_until(self, separator=serial.LF):
        return await self._socket.readuntil(separator)

    @assert_open
    async def read_all(self):
        return await self._socket.readbuffer()

    @assert_open
    async def write(self, data):
        return await self._socket.write(data)

    @assert_open
    def reset_input_buffer(self):
        self._socket.reset_input_buffer()

    @assert_open
    def reset_output_buffer(self):
        # ignored in raw tcp socket
        pass

    @assert_open
    def send_break(self, duration=0.25):
        # ignored in raw tcp socket
        pass

    # Extra interface not provided by serial.Serial

    @assert_open
    async def readlines(self, n, eol=None):
        return await self._socket.readlines(n, eol=eol)

    @assert_open
    async def writelines(self, lines):
        return await self._socket.writelines(lines)

    @assert_open
    async def write_readline(self, data, eol=None):
        return await self._socket.write_readline(data, eol=eol)

    @assert_open
    async def write_readlines(self, data, n, eol=None):
        return await self._socket.write_readlines(data, n, eol=eol)

    @assert_open
    async def writelines_readlines(self, lines, n=None, eol=None):
        return await self._socket.writelines_readlines(lines, n=n, eol=eol)
