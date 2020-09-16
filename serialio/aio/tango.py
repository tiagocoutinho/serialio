import tango.asyncio

import serial
from .base import SerialBase, SerialException


class Serial(SerialBase):

    device = None
    is_open = False

    _TIMEOUT = 3
    _PARITY = 4
    _CHARLENGTH = 5
    _STOPBITS = 6
    _BAUDRATE = 7
    _NEWLINE = 8

    _RAW = 0
    _NCHAR = 1
    _LINE = 2
    _RETRY = 3

    _PARITY_MAP = {serial.PARITY_NONE: 0, serial.PARITY_ODD: 1, serial.PARITY_EVEN: 3}

    _CHARLENGTH_MAP = {
        serial.EIGHTBITS: 0,
        serial.SEVENBITS: 1,
        serial.SIXBITS: 2,
        serial.FIVEBITS: 3,
    }

    _STOPBITS_MAP = {
        serial.STOPBITS_ONE: 0,
        serial.STOPBITS_ONE_POINT_FIVE: 1,
        serial.STOPBITS_TWO: 2,
    }

    async def open(self):
        if self.is_open:
            raise SerialException("Port is already open.")
        self.device = None
        # open
        try:
            self.device = await tango.asyncio.DeviceProxy(self.port)
        except tango.DevFailed as error:
            raise SerialException(
                "could not open tango serial port {}: {!r}".format(self.port, error)
            )
        try:
            await self._reconfigure_port()
        except:
            self.device = None
            raise
        else:
            self.is_open = True

    async def close(self):
        self.device = None
        self.is_open = False

    async def _reconfigure_port(self):
        pars = [
            self._BAUDRATE,
            self._baudrate,
            self._CHARLENGTH,
            self._CHARLENGTH_MAP[self._bytesize],
            self._PARITY,
            self._PARITY_MAP[self._parity],
            self._NEWLINE,
            ord(self._eol),
        ]
        if self._timeout is not None:
            pars.extend((self._TIMEOUT, int(self._timeout * 1000)))
        await self.device.command_inout("DevSerSetParameter", pars)

    @property
    async def in_waiting(self):
        return await self.device.command_inout("DevSerGetNChar")

    async def _read1(self, size):
        data = await self.device.command_inout("DevSerReadNBinData", size)
        return bytes(data)

    async def _read(self, size=1):
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

    async def _write1(self, data):
        return await self.device.command_inout("DevSerWriteChar", data)

    async def _write(self, data):
        d = bytes(data)
        tx_len = length = len(d)
        while tx_len > 0:
            n = await self._write1(d)
            d = d[n:]
            tx_len -= n
        return length - len(d)

    async def readline(self, eol=None):
        data = await self.device.command_inout("DevSerReadChar", self._LINE)
        return bytes(data)
