import array
import asyncio
import logging
import functools

import serial
from serial import (
    Timeout,
    SerialException,
    SerialTimeoutException,
    writeTimeoutError,
    portNotOpenError,
)


def module_symbols(mod, filter_func=str.isupper):
    """return module symbols"""
    return {k: getattr(mod, k) for k in dir(mod) if filter_func(k)}


globals().update(module_symbols(serial))


def assert_open(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.is_open:
            raise portNotOpenError
        return func(self, *args, **kwargs)

    return wrapper


def async_assert_open(func):
    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        if not self.is_open:
            raise portNotOpenError
        return await func(self, *args, **kwargs)

    return wrapper


def ensure_open(func):
    """
    Decorator helper which ensures serial line is connected before func is exectued
    """
    assert asyncio.iscoroutinefunction(func)
    name = func.__name__

    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        if not self.is_open:
            if self._auto_reconnect:
                await self.open()
            else:
                raise portNotOpenError
        timeout = kwargs.pop("timeout", self._timeout)
        coro = func(self, *args, **kwargs)
        if timeout is not None:
            coro = asyncio.wait_for(coro, timeout)
        try:
            return await coro
        except asyncio.TimeoutError as error:
            msg = "{} call timeout on '{}:{}'".format(name, self.host, self.port)
            raise ConnectionTimeoutError(msg) from error

    return wrapper


def ensure_call(func):
    """Method decorator helper"""
    assert asyncio.iscoroutinefunction(func)

    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        try:
            return await func(self, *args, **kwargs)
        except OSError:
            await self.close()
            if self._auto_reconnect:
                await self.open()
                try:
                    return await func(self, *args, **kwargs)
                except OSError:
                    await self.close()
                    raise
            else:
                raise

    return wrapper


def ensure_call_reply(func):
    func = ensure_call(func)

    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        reply = await func(self, *args, **kwargs)
        if not reply:
            await self.close()
            raise ConnectionError("Connection closed by peer")
        return reply

    return wrapper


# "for byte in data" fails for python3 as it returns ints instead of bytes
def iterbytes(b):
    """Iterate over bytes, returning bytes instead of ints"""
    if isinstance(b, memoryview):
        b = b.tobytes()
    i = 0
    while True:
        a = b[i : i + 1]
        i += 1
        if a:
            yield a
        else:
            break


class SerialBase:

    BAUDRATES = serial.SerialBase.BAUDRATES
    BYTESIZES = serial.SerialBase.BYTESIZES
    PARITIES = serial.SerialBase.PARITIES

    def __init__(
        self,
        port,
        baudrate=9600,
        bytesize=EIGHTBITS,
        parity=PARITY_NONE,
        stopbits=STOPBITS_ONE,
        timeout=None,
        xonxoff=False,
        rtscts=False,
        write_timeout=None,
        dsrdtr=False,
        inter_byte_timeout=None,
        exclusive=None,
        auto_reconnect=True,
        eol=LF,
    ):
        assert isinstance(port, str) and port
        self._port = port
        self._baudrate = baudrate
        self._bytesize = bytesize
        self._parity = parity
        self._stopbits = stopbits
        self._timeout = timeout
        self._xonxoff = xonxoff
        self._rtscts = rtscts
        self._dsrdtr = dsrdtr
        self._inter_byte_timeout = None
        self._rs485_mode = None  # disabled by default
        self._rts_state = True
        self._dtr_state = True
        self._break_state = False
        self._exclusive = exclusive
        self._auto_reconnect = auto_reconnect
        self._eol = eol
        self.logger = logging.getLogger("Serial({})".format(self._port))

    #  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -

    # to be implemented by subclasses:
    # async def open(self):
    # async def close(self):

    #  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -

    @property
    def port(self):
        """Get the current port setting"""
        return self._port

    @property
    def baudrate(self):
        """Get the current baud rate setting."""
        return self._baudrate

    async def set_baudrate(self, baudrate):
        """\
        Change baud rate. It raises a ValueError if the port is open and the
        baud rate is not possible. If the port is closed, then the value is
        accepted and the exception is raised when the port is opened.
        """
        try:
            b = int(baudrate)
        except TypeError:
            raise ValueError("Not a valid baudrate: {!r}".format(baudrate))
        else:
            if b < 0:
                raise ValueError("Not a valid baudrate: {!r}".format(baudrate))
            self._baudrate = b
            if self.is_open:
                await self._reconfigure_port()

    @property
    def bytesize(self):
        """Get the current byte size setting."""
        return self._bytesize

    async def set_bytesize(self, bytesize):
        """Change byte size."""
        if bytesize not in self.BYTESIZES:
            raise ValueError("Not a valid byte size: {!r}".format(bytesize))
        self._bytesize = bytesize
        if self.is_open:
            await self._reconfigure_port()

    @property
    def exclusive(self):
        """Get the current exclusive access setting."""
        return self._exclusive

    async def set_exclusive(self, exclusive):
        """Change the exclusive access setting."""
        self._exclusive = exclusive
        if self.is_open:
            await self._reconfigure_port()

    @property
    def parity(self):
        """Get the current parity setting."""
        return self._parity

    async def set_parity(self, parity):
        """Change parity setting."""
        if parity not in self.PARITIES:
            raise ValueError("Not a valid parity: {!r}".format(parity))
        self._parity = parity
        if self.is_open:
            await self._reconfigure_port()

    @property
    def stopbits(self):
        """Get the current stop bits setting."""
        return self._stopbits

    async def set_stopbits(self, stopbits):
        """Change stop bits size."""
        if stopbits not in self.STOPBITS:
            raise ValueError("Not a valid stop bit size: {!r}".format(stopbits))
        self._stopbits = stopbits
        if self.is_open:
            await self._reconfigure_port()

    @property
    def timeout(self):
        return self._timeout

    async def timeout(self, timeout):
        """Change timeout setting."""
        if timeout is not None:
            try:
                timeout + 1  # test if it's a number, will throw a TypeError if not...
            except TypeError:
                raise ValueError("Not a valid timeout: {!r}".format(timeout))
            if timeout < 0:
                raise ValueError("Not a valid timeout: {!r}".format(timeout))
        self._timeout = timeout
        if self.is_open:
            await self._reconfigure_port()

    @property
    def inter_byte_timeout(self):
        """Get the current inter-character timeout setting."""
        return self._inter_byte_timeout

    async def set_inter_byte_timeout(self, ic_timeout):
        """Change inter-byte timeout setting."""
        if ic_timeout is not None:
            if ic_timeout < 0:
                raise ValueError("Not a valid timeout: {!r}".format(ic_timeout))
            try:
                ic_timeout + 1  # test if it's a number, will throw a TypeError if not...
            except TypeError:
                raise ValueError("Not a valid timeout: {!r}".format(ic_timeout))

        self._inter_byte_timeout = ic_timeout
        if self.is_open:
            await self._reconfigure_port()

    @property
    def xonxoff(self):
        """Get the current XON/XOFF setting."""
        return self._xonxoff

    async def set_xonxoff(self, xonxoff):
        """Change XON/XOFF setting."""
        self._xonxoff = xonxoff
        if self.is_open:
            await self._reconfigure_port()

    @property
    def rtscts(self):
        """Get the current RTS/CTS flow control setting."""
        return self._rtscts

    async def set_rtscts(self, rtscts):
        """Change RTS/CTS flow control setting."""
        self._rtscts = rtscts
        if self.is_open:
            await self._reconfigure_port()

    @property
    def dsrdtr(self):
        """Get the current DSR/DTR flow control setting."""
        return self._dsrdtr

    async def set_dsrdtr(self, dsrdtr=None):
        """Change DsrDtr flow control setting."""
        if dsrdtr is None:
            # if not set, keep backwards compatibility and follow rtscts
            # setting
            self._dsrdtr = self._rtscts
        else:
            # if defined independently, follow its value
            self._dsrdtr = dsrdtr
        if self.is_open:
            await self._reconfigure_port()

    @property
    def rts(self):
        return self._rts_state

    async def set_rts(self, value):
        self._rts_state = value
        if self.is_open:
            await self._update_rts_state()

    @property
    def dtr(self):
        return self._dtr_state

    async def set_dtr(self, value):
        self._dtr_state = value
        if self.is_open:
            await self._update_dtr_state()

    @property
    def break_condition(self):
        return self._break_state

    async def set_break_condition(self, value):
        self._break_state = value
        if self.is_open:
            await self._update_break_state()

    #  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -
    # functions useful for RS-485 adapters

    @property
    def rs485_mode(self):
        """\
        Enable RS485 mode and apply new settings, set to None to disable.
        See serial.rs485.RS485Settings for more info about the value.
        """
        return self._rs485_mode

    async def set_rs485_mode(self, rs485_settings):
        self._rs485_mode = rs485_settings
        if self.is_open:
            await self._reconfigure_port()

    #  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -

    _SAVED_SETTINGS = (
        "baudrate",
        "bytesize",
        "parity",
        "stopbits",
        "xonxoff",
        "dsrdtr",
        "rtscts",
    )

    def get_settings(self):
        """\
        Get current port settings as a dictionary. For use with
        apply_settings().
        """
        return {key: getattr(self, "_" + key) for key in self._SAVED_SETTINGS}

    def apply_settings(self, d):
        """\
        Apply stored settings from a dictionary returned from
        get_settings(). It's allowed to delete keys from the dictionary. These
        values will simply left unchanged.
        """
        for key in self._SAVED_SETTINGS:
            if key in d and d[key] != getattr(
                self, "_" + key
            ):  # check against internal "_" value
                # set non "_" value to use properties write function
                setattr(self, key, d[key])

    #  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -

    def __repr__(self):
        """String representation of the current port settings and its state."""
        return (
            "{name}<id=0x{id:x}, open={p.is_open}>(port={p.port!r}, "
            "baudrate={p.baudrate!r}, bytesize={p.bytesize!r}, parity={p.parity!r}, "
            "stopbits={p.stopbits!r}, xonxoff={p.xonxoff!r}, "
            "rtscts={p.rtscts!r}, dsrdtr={p.dsrdtr!r})".format(
                name=self.__class__.__name__, id=id(self), p=self
            )
        )

    #  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -
    # compatibility with io library
    # pylint: disable=invalid-name,missing-docstring

    def readable(self):
        return True

    def writable(self):
        return True

    def seekable(self):
        return False

    @ensure_open
    @ensure_call_reply
    async def readinto(self, b):
        data = await self._read(len(b))
        n = len(data)
        try:
            b[:n] = data
        except TypeError:
            if not isinstance(b, array.array):
                raise
            b[:n] = array.array("b", data)
        return n

    #  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -

    @ensure_open
    @ensure_call_reply
    async def read(self, size=1):
        return await self._read(size=size)

    @ensure_open
    @ensure_call
    async def write(self, data):
        return await self._write(data)

    @ensure_open
    @ensure_call_reply
    async def readuntil(self, separator=LF, size=None):
        """\
        Read until an expected sequence is found ('\n' by default) or the size
        is exceeded.
        """
        lenterm = len(separator)
        line = bytearray()
        while True:
            c = await self._read(1)
            if c:
                line += c
                if line[-lenterm:] == separator:
                    break
                if size is not None and len(line) >= size:
                    break
            else:
                break
        return bytes(line)

    @ensure_open
    @ensure_call_reply
    async def readbuffer(self):
        """Read all bytes currently available in the buffer of the OS"""
        return await self._read(self.in_waiting)

    @ensure_open
    @ensure_call
    async def writelines(self, lines):
        return await self._write(b"".join(lines))

    @ensure_open
    @ensure_call_reply
    async def readline(self, eol=None):
        if eol is None:
            eol = self._eol
        return await self.readuntil(separator=eol)

    @ensure_open
    @ensure_call_reply
    async def readlines(self, n, eol=None):
        if eol is None:
            eol = self._eol
        return [await self.readline(eol=eol) for _ in range(n)]

    @ensure_open
    @ensure_call_reply
    async def write_readline(self, data, eol=None):
        await self.write(data)
        return await self.readline(eol=eol)

    @ensure_open
    @ensure_call_reply
    async def write_readlines(self, data, n, eol=None):
        await self.write(data)
        return await self.readlines(n, eol=eol)

    @ensure_open
    @ensure_call_reply
    async def writelines_readlines(self, lines, n=None, eol=None):
        if n is None:
            n = len(lines)
        await self.writelines(lines)
        return await self.readlines(n, eol=eol)

    @ensure_open
    async def send_break(self, duration=0.25):
        """\
        Send break condition. Timed, returns to idle state after given
        duration.
        """
        self.break_condition = True
        await asyncio.sleep(duration)
        self.break_condition = False
