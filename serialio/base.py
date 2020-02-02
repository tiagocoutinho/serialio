import io
import time
import asyncio
import logging

# "for byte in data" fails for python3 as it returns ints instead of bytes
def iterbytes(b):
    """Iterate over bytes, returning bytes instead of ints (python3)"""
    if isinstance(b, memoryview):
        b = b.tobytes()
    i = 0
    while True:
        a = b[i:i + 1]
        i += 1
        if a:
            yield a
        else:
            break


# all Python versions prior 3.x convert ``str([17])`` to '[17]' instead of '\x11'
# so a simple ``bytes(sequence)`` doesn't work for all versions
def to_bytes(seq):
    """convert a sequence to a bytes type"""
    if isinstance(seq, bytes):
        return seq
    elif isinstance(seq, bytearray):
        return bytes(seq)
    elif isinstance(seq, memoryview):
        return seq.tobytes()
    elif isinstance(seq, str):
        raise TypeError(
            'unicode strings are not supported, please encode to bytes: {!r}'.format(seq))
    else:
        # handle list of integers and bytes (one or more items) for Python 2
        # and 3
        return bytes(bytearray(seq))


# create control bytes
XON = to_bytes([17])
XOFF = to_bytes([19])

CR = to_bytes([13])
LF = to_bytes([10])


PARITY_NONE, PARITY_EVEN, PARITY_ODD, PARITY_MARK, PARITY_SPACE = 'N', 'E', 'O', 'M', 'S'
STOPBITS_ONE, STOPBITS_ONE_POINT_FIVE, STOPBITS_TWO = (1, 1.5, 2)
FIVEBITS, SIXBITS, SEVENBITS, EIGHTBITS = (5, 6, 7, 8)

PARITY_NAMES = {
    PARITY_NONE: 'None',
    PARITY_EVEN: 'Even',
    PARITY_ODD: 'Odd',
    PARITY_MARK: 'Mark',
    PARITY_SPACE: 'Space',
}


class SerialException(IOError):
    """Base class for serial port related exceptions."""


class SerialTimeoutException(SerialException):
    """Write timeouts give an exception"""


writeTimeoutError = SerialTimeoutException('Write timeout')
portNotOpenError = SerialException('Attempting to use a port that is not open')


class Timeout(object):
    """\
    Abstraction for timeout operations. Using time.monotonic() if available
    or time.time() in all other cases.

    The class can also be initialized with 0 or None, in order to support
    non-blocking and fully blocking I/O operations. The attributes
    is_non_blocking and is_infinite are set accordingly.
    """
    if hasattr(time, 'monotonic'):
        # Timeout implementation with time.monotonic(). This function is only
        # supported by Python 3.3 and above. It returns a time in seconds
        # (float) just as time.time(), but is not affected by system clock
        # adjustments.
        TIME = time.monotonic
    else:
        # Timeout implementation with time.time(). This is compatible with all
        # Python versions but has issues if the clock is adjusted while the
        # timeout is running.
        TIME = time.time

    def __init__(self, duration):
        """Initialize a timeout with given duration"""
        self.is_infinite = (duration is None)
        self.is_non_blocking = (duration == 0)
        self.duration = duration
        if duration is not None:
            self.target_time = self.TIME() + duration
        else:
            self.target_time = None

    def expired(self):
        """Return a boolean, telling if the timeout has expired"""
        return self.target_time is not None and self.time_left() <= 0

    def time_left(self):
        """Return how many seconds are left until the timeout expires"""
        if self.is_non_blocking:
            return 0
        elif self.is_infinite:
            return None
        else:
            delta = self.target_time - self.TIME()
            if delta > self.duration:
                # clock jumped, recalculate
                self.target_time = self.TIME() + self.duration
                return self.duration
            else:
                return max(0, delta)

    def restart(self, duration):
        """\
        Restart a timeout, only supported if a timeout was already set up
        before.
        """
        self.duration = duration
        self.target_time = self.TIME() + duration


class SerialBase:
    """\
    Serial port base class. Provides __init__ function and properties to
    get/set port settings.
    """

    # default values, may be overridden in subclasses that do not support all
    # values
    BAUDRATES = (50, 75, 110, 134, 150, 200, 300, 600, 1200, 1800, 2400, 4800,
                 9600, 19200, 38400, 57600, 115200, 230400, 460800, 500000,
                 576000, 921600, 1000000, 1152000, 1500000, 2000000, 2500000,
                 3000000, 3500000, 4000000)
    BYTESIZES = (FIVEBITS, SIXBITS, SEVENBITS, EIGHTBITS)
    PARITIES = (
        PARITY_NONE,
        PARITY_EVEN,
        PARITY_ODD,
        PARITY_MARK,
        PARITY_SPACE)
    STOPBITS = (STOPBITS_ONE, STOPBITS_ONE_POINT_FIVE, STOPBITS_TWO)

    def __init__(self,
                 port,
                 baudrate=9600,
                 bytesize=EIGHTBITS,
                 parity=PARITY_NONE,
                 stopbits=STOPBITS_ONE,
                 xonxoff=False,
                 rtscts=False,
                 dsrdtr=False,
                 exclusive=None,
                 eol=b'\n',
                 **kwargs):
        """\
        Initialize comm port object. If a "port" is given, then the port will be
        opened immediately. Otherwise a Serial port object in closed state
        is returned.
        """

        self.is_open = False
        self.name = port
        self._port = port
        self._baudrate = baudrate
        self._bytesize = bytesize
        self._parity = parity
        self._stopbits = stopbits
        self._xonxoff = xonxoff
        self._rtscts = rtscts
        self._dsrdtr = dsrdtr
        self._rs485_mode = None  # disabled by default
        self._rts_state = True
        self._dtr_state = True
        self._break_state = False
        self._exclusive = exclusive
        self._eol = eol
        self.logger = logging.getLogger('Serial({})'.format(self.name))
        if kwargs:
            raise ValueError(
                'unexpected keyword arguments: {!r}'.format(kwargs))

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
            raise ValueError(
                "Not a valid stop bit size: {!r}".format(stopbits))
        self._stopbits = stopbits
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

    _SAVED_SETTINGS = ('baudrate', 'bytesize', 'parity', 'stopbits', 'xonxoff',
                       'dsrdtr', 'rtscts')

    def get_settings(self):
        """\
        Get current port settings as a dictionary. For use with
        apply_settings().
        """
        return dict([(key, getattr(self, '_' + key))
                     for key in self._SAVED_SETTINGS])

    def apply_settings(self, d):
        """\
        Apply stored settings from a dictionary returned from
        get_settings(). It's allowed to delete keys from the dictionary. These
        values will simply left unchanged.
        """
        for key in self._SAVED_SETTINGS:
            if key in d and d[key] != getattr(
                    self, '_' + key):   # check against internal "_" value
                # set non "_" value to use properties write function
                setattr(self, key, d[key])

    #  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -

    def __repr__(self):
        """String representation of the current port settings and its state."""
        return '{name}<id=0x{id:x}, open={p.is_open}>(port={p.port!r}, ' \
               'baudrate={p.baudrate!r}, bytesize={p.bytesize!r}, parity={p.parity!r}, ' \
               'stopbits={p.stopbits!r}, xonxoff={p.xonxoff!r}, ' \
               'rtscts={p.rtscts!r}, dsrdtr={p.dsrdtr!r})'.format(
                   name=self.__class__.__name__, id=id(self), p=self)

    #  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -
    # compatibility with io library
    # pylint: disable=invalid-name,missing-docstring

    def readable(self):
        return True

    def writable(self):
        return True

    def seekable(self):
        return False

    async def readinto(self, b):
        data = await self.read(len(b))
        n = len(data)
        try:
            b[:n] = data
        except TypeError as err:
            import array
            if not isinstance(b, array.array):
                raise err
            b[:n] = array.array('b', data)
        return n

    async def readuntil(self, separator=LF):
        """\
        Read until an expected sequence is found ('\n' by default) or the size
        is exceeded.
        """
        lenterm = len(separator)
        line = bytearray()
        while True:
            c = await self.read(1)
            if c:
                line += c
                if line[-lenterm:] == separator:
                    break
            else:
                break
        return bytes(line)

    async def readbuffer(self):
        """Read all bytes currently available in the buffer of the OS"""
        return await self.read(self.in_waiting)

    async def writelines(self, lines):
        return await self.write(b''.join(lines))

    async def readline(self, eol=None):
        if eol is None:
            eol = self._eol
        return await self.readuntil(separator=eol)

    async def readlines(self, n, eol=None):
        if eol is None:
            eol = self._eol
        return [await self.readline(eol=eol) for _ in range(n)]

    async def write_readline(self, data, eol=None):
        await self.write(data)
        return await self.readline(eol=eol)

    async def write_readlines(self, data, n, eol=None):
        await self.write(data)
        return await self.readlines(n, eol=eol)

    async def writelines_readlines(self, lines, n=None, eol=None):
        if n is None:
            n = len(lines)
        await self.writelines(lines)
        return await self.readlines(n, eol=eol)

    async def send_break(self, duration=0.25):
        """\
        Send break condition. Timed, returns to idle state after given
        duration.
        """
        if not self.is_open:
            raise portNotOpenError
        self.break_condition = True
        await asyncio.sleep(duration)
        self.break_condition = False
