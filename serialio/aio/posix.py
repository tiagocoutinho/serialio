import os
import errno
import fcntl
import struct
import asyncio
import termios

import serial.serialposix
from serial.serialposix import PlatformSpecific

from .base import (
    SerialBase,
    SerialException,
    module_symbols,
    assert_open,
    async_assert_open,
)

globals().update(module_symbols(serial.serialposix))
TIOCM_DTR_str = serial.serialposix.TIOCM_DTR_str
TIOCM_RTS_str = serial.serialposix.TIOCM_RTS_str
TIOCM_zero_str = serial.serialposix.TIOCM_zero_str


class Serial(SerialBase, PlatformSpecific):
    @property
    def _loop(self):
        return asyncio.get_running_loop()

    async def _read1(self, size):
        loop, read_event = self._loop, asyncio.Event()
        loop.add_reader(self.fd, read_event.set)
        try:
            await read_event.wait()
        finally:
            loop.remove_reader(self.fd)
        return os.read(self.fd, size)

    async def _write1(self, data):
        loop, write_event = self._loop, asyncio.Event()
        loop.add_writer(self.fd, write_event.set)
        try:
            await write_event.wait()
        finally:
            loop.remove_writer(self.fd)
        return os.write(self.fd, data)

    async def open(self):
        """\
        Open port with current settings. This may throw a SerialException
        if the port cannot be opened."""
        if self._port is None:
            raise SerialException("Port must be configured before it can be used.")
        if self.is_open:
            raise SerialException("Port is already open.")
        self.fd = None
        # open
        try:
            self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as msg:
            self.fd = None
            raise SerialException(
                msg.errno, "could not open port {}: {}".format(self._port, msg)
            )
        # ~ fcntl.fcntl(self.fd, fcntl.F_SETFL, 0)  # set blocking

        try:
            await self._reconfigure_port(force_update=True)
        except:
            try:
                os.close(self.fd)
            except:
                # ignore any exception when closing the port
                # also to keep original exception that happened when setting up
                pass
            self.fd = None
            raise
        else:
            self.is_open = True
        try:
            if not self._dsrdtr:
                await self._update_dtr_state()
            if not self._rtscts:
                await self._update_rts_state()
        except IOError as e:
            if e.errno in (errno.EINVAL, errno.ENOTTY):
                # ignore Invalid argument and Inappropriate ioctl
                pass
            else:
                raise
        await self.reset_input_buffer()

    async def _reconfigure_port(self, force_update=False):
        """Set communication parameters on opened port."""
        if self.fd is None:
            raise SerialException("Can only operate on a valid file descriptor")

        # if exclusive lock is requested, create it before we modify anything else
        if self._exclusive is not None:
            if self._exclusive:
                try:
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except IOError as msg:
                    raise SerialException(
                        msg.errno,
                        "Could not exclusively lock port {}: {}".format(
                            self._port, msg
                        ),
                    )
            else:
                fcntl.flock(self.fd, fcntl.LOCK_UN)

        custom_baud = None

        vmin = vtime = 0  # timeout is done via select
        if self._inter_byte_timeout is not None:
            vmin = 1
            vtime = int(self._inter_byte_timeout * 10)
        try:
            orig_attr = termios.tcgetattr(self.fd)
            iflag, oflag, cflag, lflag, ispeed, ospeed, cc = orig_attr
        except termios.error as msg:  # if a port is nonexistent but has a /dev file, it'll fail here
            raise SerialException("Could not configure port: {}".format(msg))
        # set up raw mode / no echo / binary
        cflag |= termios.CLOCAL | termios.CREAD
        lflag &= ~(
            termios.ICANON
            | termios.ECHO
            | termios.ECHOE
            | termios.ECHOK
            | termios.ECHONL
            | termios.ISIG
            | termios.IEXTEN
        )  # |termios.ECHOPRT
        for flag in ("ECHOCTL", "ECHOKE"):  # netbsd workaround for Erk
            if hasattr(termios, flag):
                lflag &= ~getattr(termios, flag)

        oflag &= ~(termios.OPOST | termios.ONLCR | termios.OCRNL)
        iflag &= ~(termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IGNBRK)
        if hasattr(termios, "IUCLC"):
            iflag &= ~termios.IUCLC
        if hasattr(termios, "PARMRK"):
            iflag &= ~termios.PARMRK

        # setup baud rate
        try:
            ispeed = ospeed = getattr(termios, "B{}".format(self._baudrate))
        except AttributeError:
            try:
                ispeed = ospeed = self.BAUDRATE_CONSTANTS[self._baudrate]
            except KeyError:
                # ~ raise ValueError('Invalid baud rate: %r' % self._baudrate)
                # may need custom baud rate, it isn't in our list.
                ispeed = ospeed = getattr(termios, "B38400")
                try:
                    custom_baud = int(self._baudrate)  # store for later
                except ValueError:
                    raise ValueError("Invalid baud rate: {!r}".format(self._baudrate))
                else:
                    if custom_baud < 0:
                        raise ValueError(
                            "Invalid baud rate: {!r}".format(self._baudrate)
                        )

        # setup char len
        cflag &= ~termios.CSIZE
        if self._bytesize == 8:
            cflag |= termios.CS8
        elif self._bytesize == 7:
            cflag |= termios.CS7
        elif self._bytesize == 6:
            cflag |= termios.CS6
        elif self._bytesize == 5:
            cflag |= termios.CS5
        else:
            raise ValueError("Invalid char len: {!r}".format(self._bytesize))
        # setup stop bits
        if self._stopbits == serial.STOPBITS_ONE:
            cflag &= ~(termios.CSTOPB)
        elif self._stopbits == serial.STOPBITS_ONE_POINT_FIVE:
            cflag |= (
                termios.CSTOPB
            )  # XXX same as TWO.. there is no POSIX support for 1.5
        elif self._stopbits == serial.STOPBITS_TWO:
            cflag |= termios.CSTOPB
        else:
            raise ValueError(
                "Invalid stop bit specification: {!r}".format(self._stopbits)
            )
        # setup parity
        iflag &= ~(termios.INPCK | termios.ISTRIP)
        if self._parity == serial.PARITY_NONE:
            cflag &= ~(termios.PARENB | termios.PARODD | CMSPAR)
        elif self._parity == serial.PARITY_EVEN:
            cflag &= ~(termios.PARODD | CMSPAR)
            cflag |= termios.PARENB
        elif self._parity == serial.PARITY_ODD:
            cflag &= ~CMSPAR
            cflag |= termios.PARENB | termios.PARODD
        elif self._parity == serial.PARITY_MARK and CMSPAR:
            cflag |= termios.PARENB | CMSPAR | termios.PARODD
        elif self._parity == serial.PARITY_SPACE and CMSPAR:
            cflag |= termios.PARENB | CMSPAR
            cflag &= ~(termios.PARODD)
        else:
            raise ValueError("Invalid parity: {!r}".format(self._parity))
        # setup flow control
        # xonxoff
        if hasattr(termios, "IXANY"):
            if self._xonxoff:
                iflag |= termios.IXON | termios.IXOFF  # |termios.IXANY)
            else:
                iflag &= ~(termios.IXON | termios.IXOFF | termios.IXANY)
        else:
            if self._xonxoff:
                iflag |= termios.IXON | termios.IXOFF
            else:
                iflag &= ~(termios.IXON | termios.IXOFF)
        # rtscts
        if hasattr(termios, "CRTSCTS"):
            if self._rtscts:
                cflag |= termios.CRTSCTS
            else:
                cflag &= ~(termios.CRTSCTS)
        elif hasattr(termios, "CNEW_RTSCTS"):  # try it with alternate constant name
            if self._rtscts:
                cflag |= termios.CNEW_RTSCTS
            else:
                cflag &= ~(termios.CNEW_RTSCTS)
        # XXX should there be a warning if setting up rtscts (and xonxoff etc) fails??

        # buffer
        # vmin "minimal number of characters to be read. 0 for non blocking"
        if vmin < 0 or vmin > 255:
            raise ValueError("Invalid vmin: {!r}".format(vmin))
        cc[termios.VMIN] = vmin
        # vtime
        if vtime < 0 or vtime > 255:
            raise ValueError("Invalid vtime: {!r}".format(vtime))
        cc[termios.VTIME] = vtime
        # activate settings
        if (
            force_update
            or [iflag, oflag, cflag, lflag, ispeed, ospeed, cc] != orig_attr
        ):
            termios.tcsetattr(
                self.fd,
                termios.TCSANOW,
                [iflag, oflag, cflag, lflag, ispeed, ospeed, cc],
            )

        # apply custom baud rate, if any
        if custom_baud is not None:
            self._set_special_baudrate(custom_baud)

        if self._rs485_mode is not None:
            self._set_rs485_mode(self._rs485_mode)

    async def close(self):
        """Close port"""
        if self.is_open:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            self.is_open = False

    #  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -

    @property
    def in_waiting(self):
        """Return the number of bytes currently in the input buffer."""
        # ~ s = fcntl.ioctl(self.fd, termios.FIONREAD, TIOCM_zero_str)
        s = fcntl.ioctl(self.fd, TIOCINQ, TIOCM_zero_str)
        return struct.unpack("I", s)[0]

    @async_assert_open
    async def read(self, size=1):
        read = bytearray()
        while len(read) < size:
            buf = await self._read1(size)
            if not buf:
                # Disconnected devices, at least on Linux, show the
                # behavior that they are always ready to read immediately
                # but reading returns nothing.
                raise serial.SerialException(
                    "device reports readiness to read but returned no data "
                    "(device disconnected or multiple access on port?)"
                )
            read.extend(buf)
        return bytes(read)

    @async_assert_open
    async def write(self, data):
        d = bytes(data)
        tx_len = length = len(d)
        while tx_len > 0:
            n = await self._write1(d)
            d = d[n:]
            tx_len -= n
        return length - len(d)

    @async_assert_open
    async def flush(self):
        """\
        Flush of file like objects. In this case, wait until all data
        is written.
        """
        termios.tcdrain(self.fd)

    @async_assert_open
    async def reset_input_buffer(self):
        """Clear input buffer, discarding all that is in the buffer."""
        termios.tcflush(self.fd, termios.TCIFLUSH)

    @async_assert_open
    async def reset_output_buffer(self):
        """\
        Clear output buffer, aborting the current output and discarding all
        that is in the buffer.
        """
        termios.tcflush(self.fd, termios.TCOFLUSH)

    @async_assert_open
    async def send_break(self, duration=0.25):
        """\
        Send break condition. Timed, returns to idle state after given
        duration.
        """
        termios.tcsendbreak(self.fd, int(duration / 0.25))

    async def _update_rts_state(self):
        """Set terminal status line: Request To Send"""
        if self._rts_state:
            fcntl.ioctl(self.fd, TIOCMBIS, TIOCM_RTS_str)
        else:
            fcntl.ioctl(self.fd, TIOCMBIC, TIOCM_RTS_str)

    async def _update_dtr_state(self):
        """Set terminal status line: Data Terminal Ready"""
        if self._dtr_state:
            fcntl.ioctl(self.fd, TIOCMBIS, TIOCM_DTR_str)
        else:
            fcntl.ioctl(self.fd, TIOCMBIC, TIOCM_DTR_str)

    @property
    @async_assert_open
    async def cts(self):
        """Read terminal status line: Clear To Send"""
        s = fcntl.ioctl(self.fd, TIOCMGET, TIOCM_zero_str)
        return struct.unpack("I", s)[0] & TIOCM_CTS != 0

    @property
    @async_assert_open
    async def dsr(self):
        """Read terminal status line: Data Set Ready"""
        s = fcntl.ioctl(self.fd, TIOCMGET, TIOCM_zero_str)
        return struct.unpack("I", s)[0] & TIOCM_DSR != 0

    @property
    @async_assert_open
    def ri(self):
        """Read terminal status line: Ring Indicator"""
        s = fcntl.ioctl(self.fd, TIOCMGET, TIOCM_zero_str)
        return struct.unpack("I", s)[0] & TIOCM_RI != 0

    @property
    @async_assert_open
    async def cd(self):
        """Read terminal status line: Carrier Detect"""
        s = fcntl.ioctl(self.fd, TIOCMGET, TIOCM_zero_str)
        return struct.unpack("I", s)[0] & TIOCM_CD != 0

    # - - platform specific - - - -

    @property
    def out_waiting(self):
        """Return the number of bytes currently in the output buffer."""
        # ~ s = fcntl.ioctl(self.fd, termios.FIONREAD, TIOCM_zero_str)
        s = fcntl.ioctl(self.fd, TIOCOUTQ, TIOCM_zero_str)
        return struct.unpack("I", s)[0]

    @assert_open
    def fileno(self):
        """\
        For easier use of the serial port instance with select.
        WARNING: this function is not portable to different platforms!
        """
        return self.fd

    @assert_open
    def set_input_flow_control(self, enable=True):
        """\
        Manually control flow - when software flow control is enabled.
        This will send XON (true) or XOFF (false) to the other device.
        WARNING: this function is not portable to different platforms!
        """
        termios.tcflow(self.fd, termios.TCION if enable else termios.TCIOFF)

    @assert_open
    def set_output_flow_control(self, enable=True):
        """\
        Manually control flow of outgoing data - when hardware or software flow
        control is enabled.
        WARNING: this function is not portable to different platforms!
        """
        termios.tcflow(self.fd, termios.TCOON if enable else termios.TCOOFF)
