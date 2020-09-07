import socket
import struct
import asyncio
import logging

import urllib.parse

import serial.rfc2217

from .base import (
    SerialBase,
    SerialException,
    portNotOpenError,
    Timeout,
    module_symbols,
    assert_open,
    async_assert_open,
    iterbytes,
)


globals().update(module_symbols(serial.rfc2217))


IPTOS_NORMAL = 0x0
IPTOS_LOWDELAY = 0x10
IPTOS_THROUGHPUT = 0x08
IPTOS_RELIABILITY = 0x04
IPTOS_MINCOST = 0x02


log = logging.getLogger("serialio.rfc2217")


class TelnetOption(object):
    """Manage a single telnet option, keeps track of DO/DONT WILL/WONT."""

    def __init__(
        self,
        connection,
        name,
        option,
        send_yes,
        send_no,
        ack_yes,
        ack_no,
        initial_state,
        activation_callback=None,
        option_changed_callback=None,
    ):
        """\
        Initialize option.
        :param connection: connection used to transmit answers
        :param name: a readable name for debug outputs
        :param send_yes: what to send when option is to be enabled.
        :param send_no: what to send when option is to be disabled.
        :param ack_yes: what to expect when remote agrees on option.
        :param ack_no: what to expect when remote disagrees on option.
        :param initial_state: options initialized with REQUESTED are tried to
            be enabled on startup. use INACTIVE for all others.
        """
        self.connection = connection
        self.name = name
        self.option = option
        self.send_yes = send_yes
        self.send_no = send_no
        self.ack_yes = ack_yes
        self.ack_no = ack_no
        self.state = initial_state
        self.active = False
        self.activation_callback = activation_callback or (lambda: None)
        self.option_changed_callback = option_changed_callback or (lambda o: None)

    def __repr__(self):
        """String for debug outputs"""
        return "{o.name}:{o.active}({o.state})".format(o=self)

    async def process_incoming(self, command):
        """\
        A DO/DONT/WILL/WONT was received for this option, update state and
        answer when needed.
        """
        if command == self.ack_yes:
            if self.state is REQUESTED:
                self.state = ACTIVE
                self.active = True
                self.activation_callback()
            elif self.state is ACTIVE:
                pass
            elif self.state is INACTIVE:
                self.state = ACTIVE
                await self.connection.telnet_send_option(self.send_yes, self.option)
                self.active = True
                self.activation_callback()
            elif self.state is REALLY_INACTIVE:
                await self.connection.telnet_send_option(self.send_no, self.option)
            else:
                raise ValueError("option in illegal state {!r}".format(self))
        elif command == self.ack_no:
            if self.state is REQUESTED:
                self.state = INACTIVE
                self.active = False
            elif self.state is ACTIVE:
                self.state = INACTIVE
                await self.connection.telnet_send_option(self.send_no, self.option)
                self.active = False
            elif self.state is INACTIVE:
                pass
            elif self.state is REALLY_INACTIVE:
                pass
            else:
                raise ValueError("option in illegal state {!r}".format(self))
        self.option_changed_callback(self)


class TelnetSubnegotiation(object):
    """\
    A object to handle subnegotiation of options. In this case actually
    sub-sub options for RFC 2217. It is used to track com port options.
    """

    def __init__(self, connection, name, option, ack_option=None):
        if ack_option is None:
            ack_option = option
        self.connection = connection
        self.name = name
        self.option = option
        self.value = None
        self.ack_option = ack_option
        self.state = INACTIVE
        self.active_event = asyncio.Event()

    def __repr__(self):
        """String for debug outputs."""
        return "{sn.name}:{sn.state}".format(sn=self)

    def prepare(self, value):
        self.value = value
        self.state = REQUESTED
        self.active_event.clear()
        return self.option, self.value

    async def set(self, value):
        """\
        Request a change of the value. a request is sent to the server. if
        the client needs to know if the change is performed he has to check the
        state of this object.
        """
        option, value = self.prepare(value)
        self.connection.logger.debug(
            "SB Requesting {} -> {!r}".format(self.name, value)
        )
        await self.connection.rfc2217_send_subnegotiation(option, value)

    def is_ready(self):
        """\
        Check if answer from server has been received. when server rejects
        the change, raise a ValueError.
        """
        if self.state == REALLY_INACTIVE:
            raise ValueError("remote rejected value for option {!r}".format(self.name))
        return self.state == ACTIVE

    # add property to have a similar interface as TelnetOption
    active = property(is_ready)

    async def wait(self):
        """\
        Wait until the subnegotiation has been acknowledged. It
        can also throw a value error when the answer from the server does not
        match the value sent.
        """
        # TODO implement timeout
        await self.active_event.wait()

    def check_answer(self, suboption):
        """\
        Check an incoming subnegotiation block. The parameter already has
        cut off the header like sub option number and com port option value.
        """
        if self.value == suboption[: len(self.value)]:
            self.state = ACTIVE
            self.active_event.set()
        else:
            # error propagation done in is_ready
            self.state = REALLY_INACTIVE
            self.active_event.clear()
        self.connection.logger.debug(
            "SB Answer {} -> {!r} -> {}".format(self.name, suboption, self.state)
        )


async def _open_connection(host, port, no_delay=True, tos=IPTOS_LOWDELAY):
    reader, writer = await asyncio.open_connection(host, port)
    sock = writer.transport.get_extra_info("socket")
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_IP, socket.IP_TOS, tos)
    return reader, writer


# It was very tempting to inherit from serial.rfc2217.Serial.
# This would result in being extremely dependent on its implementation details.
# There would be a high risk that this would become incompatible with several
# versions of serial library.


class Serial(SerialBase):
    """Serial port implementation for RFC 2217 remote serial ports."""

    BAUDRATES = serial.rfc2217.Serial.BAUDRATES

    def __init__(self, *args, **kwargs):
        self._thread = None
        self._socket = None
        self._linestate = 0
        self._modemstate = None
        self._modemstate_timeout = Timeout(-1)
        self._remote_suspend_flow = False
        self._write_lock = None
        self._ignore_set_control_answer = False
        self._poll_modem_state = False
        self._network_timeout = 3
        self._telnet_options = None
        self._rfc2217_port_settings = None
        self._rfc2217_options = None
        self._read_buffer = None
        self._no_delay = kwargs.pop("no_delay", True)
        self._tos = kwargs.pop("tos", IPTOS_LOWDELAY)
        self.logger = log
        super().__init__(*args, **kwargs)

    async def _open_connection(self, host, port):
        return await _open_connection(
            host, port, no_delay=self._no_delay, tos=self._tos
        )

    async def open(self):
        self._ignore_set_control_answer = False
        self._poll_modem_state = False
        self._network_timeout = 1
        if self._port is None:
            raise SerialException("Port must be configured before it can be used.")
        if self.is_open:
            raise SerialException("Port is already open.")
        host, port = self.from_url(self._port)

        try:
            self._socket = await self._open_connection(host, port)
        except Exception as msg:
            self._socket = None
            raise SerialException("Could not open port {}: {}".format(self.port, msg))

        # use a thread save queue as buffer. it also simplifies implementing
        # the read timeout
        self._read_buffer = asyncio.Queue()
        # to ensure that user writes does not interfere with internal
        # telnet/rfc2217 options establish a lock
        self._write_lock = asyncio.Lock()

        mandadory_done = asyncio.Event()

        def on_mandadory_event(opt):
            if sum(o.active for o in mandadory_options) == sum(
                o.state != INACTIVE for o in mandadory_options
            ):
                mandadory_done.set()

        # name the following separately so that, below, a check can be easily
        # done
        mandadory_options = [
            TelnetOption(
                self,
                "we-BINARY",
                BINARY,
                WILL,
                WONT,
                DO,
                DONT,
                INACTIVE,
                option_changed_callback=on_mandadory_event,
            ),
            TelnetOption(
                self,
                "we-RFC2217",
                COM_PORT_OPTION,
                WILL,
                WONT,
                DO,
                DONT,
                REQUESTED,
                option_changed_callback=on_mandadory_event,
            ),
        ]
        # all supported telnet options
        self._telnet_options = [
            TelnetOption(self, "ECHO", ECHO, DO, DONT, WILL, WONT, REQUESTED),
            TelnetOption(self, "we-SGA", SGA, WILL, WONT, DO, DONT, REQUESTED),
            TelnetOption(self, "they-SGA", SGA, DO, DONT, WILL, WONT, REQUESTED),
            TelnetOption(self, "they-BINARY", BINARY, DO, DONT, WILL, WONT, INACTIVE),
            TelnetOption(
                self, "they-RFC2217", COM_PORT_OPTION, DO, DONT, WILL, WONT, REQUESTED
            ),
        ] + mandadory_options
        # RFC 2217 specific states
        # COM port settings
        self._rfc2217_port_settings = {
            "baudrate": TelnetSubnegotiation(
                self, "baudrate", SET_BAUDRATE, SERVER_SET_BAUDRATE
            ),
            "datasize": TelnetSubnegotiation(
                self, "datasize", SET_DATASIZE, SERVER_SET_DATASIZE
            ),
            "parity": TelnetSubnegotiation(
                self, "parity", SET_PARITY, SERVER_SET_PARITY
            ),
            "stopsize": TelnetSubnegotiation(
                self, "stopsize", SET_STOPSIZE, SERVER_SET_STOPSIZE
            ),
        }
        # There are more subnegotiation objects, combine all in one dictionary
        # for easy access
        self._rfc2217_options = {
            "purge": TelnetSubnegotiation(self, "purge", PURGE_DATA, SERVER_PURGE_DATA),
            "control": TelnetSubnegotiation(
                self, "control", SET_CONTROL, SERVER_SET_CONTROL
            ),
        }
        self._rfc2217_options.update(self._rfc2217_port_settings)
        # cache for line and modem states that the server sends to us
        self._linestate = 0
        self._modemstate = None
        self._modemstate_timeout = Timeout(-1)
        # RFC 2217 flow control between server and client
        self._remote_suspend_flow = False

        self.is_open = True
        self._thread = asyncio.create_task(self._telnet_read_loop())

        try:  # must clean-up if open fails
            # negotiate Telnet/RFC 2217 -> send initial requests
            opts = [
                (option.send_yes, option.option)
                for option in self._telnet_options
                if option.state is REQUESTED
            ]
            await self.telnet_send_options(opts)

            # now wait until important options are negotiated
            try:
                await asyncio.wait_for(mandadory_done.wait(), self._network_timeout)
            except asyncio.TimeoutError:
                raise SerialException(
                    "Remote does not seem to support RFC2217 or BINARY mode {!r}".format(
                        mandadory_options
                    )
                )
            self.logger.info("Negotiated options: {}".format(self._telnet_options))

            tasks = []
            # fine, go on, set RFC 2271 specific things
            await self._reconfigure_port()
            # all things set up get, now a clean start
            if not self._dsrdtr:
                await self._update_dtr_state()
            if not self._rtscts:
                await self._update_rts_state()
            await self.reset_input_buffer()
            await self.reset_output_buffer()
        except BaseException:
            await self.close()
            raise

    async def _reconfigure_port(self):
        """Set communication parameters on opened port."""
        if self._socket is None:
            raise SerialException("Can only operate on open ports")

        # Setup the connection
        # to get good performance, all parameter changes are sent first...
        if not 0 < self._baudrate < 2 ** 32:
            raise ValueError("invalid baudrate: {!r}".format(self._baudrate))
        await self._rfc2217_port_settings["baudrate"].set(
            struct.pack(b"!I", self._baudrate)
        )
        await self._rfc2217_port_settings["datasize"].set(
            struct.pack(b"!B", self._bytesize)
        )
        await self._rfc2217_port_settings["parity"].set(
            struct.pack(b"!B", RFC2217_PARITY_MAP[self._parity])
        )
        await self._rfc2217_port_settings["stopsize"].set(
            struct.pack(b"!B", RFC2217_STOPBIT_MAP[self._stopbits])
        )

        # and now wait until parameters are active
        items = list(self._rfc2217_port_settings.values())
        self.logger.debug("Negotiating settings: {}".format(items))

        async def wait():
            for o in items:
                await o.active_event.wait()

        try:
            await asyncio.wait_for(wait(), self._network_timeout)
        except asyncio.TimeoutError:
            raise SerialException(
                "Remote does not accept parameter change (RFC2217): {!r}".format(items)
            )
        self.logger.info("Negotiated settings: {}".format(items))
        if self._rtscts and self._xonxoff:
            raise ValueError("xonxoff and rtscts together are not supported")
        elif self._rtscts:
            await self.rfc2217_set_control(SET_CONTROL_USE_HW_FLOW_CONTROL)
        elif self._xonxoff:
            await self.rfc2217_set_control(SET_CONTROL_USE_SW_FLOW_CONTROL)
        else:
            await self.rfc2217_set_control(SET_CONTROL_USE_NO_FLOW_CONTROL)

    async def close(self):
        """Close port"""
        self.is_open = False
        if self._socket:
            writer = self._socket[1]
            try:
                writer.close()
                if hasattr(writer, "wait_closed"):
                    await writer.wait_closed()
            except BaseException:
                # ignore errors.
                pass
        if self._thread:
            # XXX more than socket timeout
            await asyncio.wait_for(self._thread, 7)
            self._thread = None
            # in case of quick reconnects, give the server some time
            await asyncio.sleep(0.3)
        self._socket = None

    def from_url(self, url):
        """\
        extract host and port from an URL string, other settings are extracted
        an stored in instance
        """
        parts = urllib.parse.urlsplit(url)
        try:
            # process options now, directly altering self
            for option, values in urllib.parse.parse_qs(parts.query, True).items():
                if option == "logging":
                    self.logger.setLevel(LOGGER_LEVELS[values[0]])
                    self.logger.debug("enabled logging")
                elif option == "ign_set_control":
                    self._ignore_set_control_answer = True
                elif option == "poll_modem":
                    self._poll_modem_state = True
                elif option == "timeout":
                    self._network_timeout = float(values[0])
                else:
                    raise ValueError("unknown option: {!r}".format(option))
            if not 0 <= parts.port < 65536:
                raise ValueError("port not in range 0...65535")
        except ValueError as e:
            raise SerialException(
                "expected a string in the form "
                '"[rfc2217://]<host>:<port>[?option[&option...]]": {}'.format(e)
            )
        return (parts.hostname, parts.port)

    #  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -

    @property
    @assert_open
    def in_waiting(self):
        """Return the number of bytes currently in the input buffer."""
        return self._read_buffer.qsize()

    async def read(self, size=1):
        """\
        Read size bytes from the serial port. It will block until the requested
        number of bytes is read.
        """
        return await self._read(size=size)

    @async_assert_open
    async def _read(self, size=1):
        data = bytearray()
        while len(data) < size:
            if self._thread is None or self._thread.done():
                raise SerialException("connection failed (reader thread died)")
            buf = await self._read_buffer.get()
            if buf is None:
                break
            data += buf
        return bytes(data)

    @async_assert_open
    async def write(self, data):
        """\
        Output the given byte string over the serial port. Can block if the
        connection is blocked. May raise SerialException if the connection is
        closed.
        """
        try:
            await self._internal_raw_write(bytes(data).replace(IAC, IAC_DOUBLED))
        except socket.error as e:
            raise SerialException("connection failed (socket error): {}".format(e))
        return len(data)

    @async_assert_open
    async def reset_input_buffer(self):
        """Clear input buffer, discarding all that is in the buffer."""
        await self.rfc2217_send_purge(PURGE_RECEIVE_BUFFER)
        # empty read buffer
        while self._read_buffer.qsize():
            self._read_buffer.get(False)

    @async_assert_open
    async def reset_output_buffer(self):
        """\
        Clear output buffer, aborting the current output and
        discarding all that is in the buffer.
        """
        await self.rfc2217_send_purge(PURGE_TRANSMIT_BUFFER)

    @async_assert_open
    async def _update_break_state(self):
        """\
        Set break: Controls TXD. When active, to transmitting is
        possible.
        """
        self.logger.info(
            "set BREAK to {}".format("active" if self._break_state else "inactive")
        )
        if self._break_state:
            await self.rfc2217_set_control(SET_CONTROL_BREAK_ON)
        else:
            await self.rfc2217_set_control(SET_CONTROL_BREAK_OFF)

    @async_assert_open
    async def _update_rts_state(self):
        """Set terminal status line: Request To Send."""
        self.logger.info(
            "set RTS to {}".format("active" if self._rts_state else "inactive")
        )
        if self._rts_state:
            await self.rfc2217_set_control(SET_CONTROL_RTS_ON)
        else:
            await self.rfc2217_set_control(SET_CONTROL_RTS_OFF)

    @async_assert_open
    async def _update_dtr_state(self):
        """Set terminal status line: Data Terminal Ready."""
        self.logger.info(
            "set DTR to {}".format("active" if self._dtr_state else "inactive")
        )
        if self._dtr_state:
            await self.rfc2217_set_control(SET_CONTROL_DTR_ON)
        else:
            await self.rfc2217_set_control(SET_CONTROL_DTR_OFF)

    @property
    @async_assert_open
    async def cts(self):
        """Read terminal status line: Clear To Send."""
        return bool(await self.get_modem_state() & MODEMSTATE_MASK_CTS)

    @property
    @async_assert_open
    async def dsr(self):
        """Read terminal status line: Data Set Ready."""
        return bool(await self.get_modem_state() & MODEMSTATE_MASK_DSR)

    @property
    @async_assert_open
    async def ri(self):
        """Read terminal status line: Ring Indicator."""
        return bool(await self.get_modem_state() & MODEMSTATE_MASK_RI)

    @property
    @async_assert_open
    async def cd(self):
        """Read terminal status line: Carrier Detect."""
        return bool(await self.get_modem_state() & MODEMSTATE_MASK_CD)

    # - - - RFC2217 specific - - -

    async def _telnet_read_loop(self):
        """Read loop for the socket."""
        mode = M_NORMAL
        suboption = None
        try:
            while self.is_open:
                reader = self._socket[0]
                try:
                    data = await reader.read(1024)
                except socket.timeout:
                    # just need to get out of recv form time to time to check if
                    # still alive
                    continue
                except socket.error as e:
                    # connection fails -> terminate loop
                    self.logger.debug("socket error in reader thread: {}".format(e))
                    await self._read_buffer.put(None)
                    break
                self.logger.debug("RECV %r", data)
                if not data:
                    await self._read_buffer.put(None)
                    break  # lost connection
                for byte in iterbytes(data):
                    if mode == M_NORMAL:
                        # interpret as command or as data
                        if byte == IAC:
                            mode = M_IAC_SEEN
                        else:
                            # store data in read buffer or sub option buffer
                            # depending on state
                            if suboption is not None:
                                suboption += byte
                            else:
                                await self._read_buffer.put(byte)
                    elif mode == M_IAC_SEEN:
                        if byte == IAC:
                            # interpret as command doubled -> insert character
                            # itself
                            if suboption is not None:
                                suboption += IAC
                            else:
                                await self._read_buffer.put(IAC)
                            mode = M_NORMAL
                        elif byte == SB:
                            # sub option start
                            suboption = bytearray()
                            mode = M_NORMAL
                        elif byte == SE:
                            # sub option end -> process it now
                            self._telnet_process_subnegotiation(bytes(suboption))
                            suboption = None
                            mode = M_NORMAL
                        elif byte in (DO, DONT, WILL, WONT):
                            # negotiation
                            telnet_command = byte
                            mode = M_NEGOTIATE
                        else:
                            # other telnet commands
                            self._telnet_process_command(byte)
                            mode = M_NORMAL
                    elif (
                        mode == M_NEGOTIATE
                    ):  # DO, DONT, WILL, WONT was received, option now following
                        await self._telnet_negotiate_option(telnet_command, byte)
                        mode = M_NORMAL
        finally:
            self._thread = None
            self.logger.debug("read thread terminated")

    # - incoming telnet commands and options

    def _telnet_process_command(self, command):
        """Process commands other than DO, DONT, WILL, WONT."""
        # Currently none. RFC2217 only uses negotiation and subnegotiation.
        self.logger.warning("ignoring Telnet command: {!r}".format(command))

    async def _telnet_negotiate_option(self, command, option):
        """Process incoming DO, DONT, WILL, WONT."""
        # check our registered telnet options and forward command to them
        # they know themselves if they have to answer or not
        known = False
        for item in self._telnet_options:
            # can have more than one match! as some options are duplicated for
            # 'us' and 'them'
            if item.option == option:
                await item.process_incoming(command)
                known = True
        if not known:
            # handle unknown options
            # only answer to positive requests and deny them
            if command == WILL or command == DO:
                await self.telnet_send_option(
                    (DONT if command == WILL else WONT), option
                )
                self.logger.warning("rejected Telnet option: {!r}".format(option))

    def _telnet_process_subnegotiation(self, suboption):
        """Process subnegotiation, the data between IAC SB and IAC SE."""
        if suboption[0:1] == COM_PORT_OPTION:
            option = suboption[1:2]
            if option == SERVER_NOTIFY_LINESTATE and len(suboption) >= 3:
                self._linestate = ord(suboption[2:3])  # ensure it is a number
                self.logger.info("NOTIFY_LINESTATE: {}".format(self._linestate))
            elif option == SERVER_NOTIFY_MODEMSTATE and len(suboption) >= 3:
                self._modemstate = ord(suboption[2:3])  # ensure it is a number
                self.logger.info("NOTIFY_MODEMSTATE: {}".format(self._modemstate))
                # update time when we think that a poll would make sense
                self._modemstate_timeout.restart(0.3)
            elif option == FLOWCONTROL_SUSPEND:
                self._remote_suspend_flow = True
            elif option == FLOWCONTROL_RESUME:
                self._remote_suspend_flow = False
            else:
                for item in self._rfc2217_options.values():
                    if item.ack_option == option:
                        # ~ print "processing COM_PORT_OPTION: %r" % list(suboption[1:])
                        item.check_answer(bytes(suboption[2:]))
                        break
                else:
                    self.logger.warning(
                        "ignoring COM_PORT_OPTION: {!r}".format(suboption)
                    )
        else:
            self.logger.warning("ignoring subnegotiation: {!r}".format(suboption))

    # - outgoing telnet commands and options

    async def _internal_raw_write(self, data):
        """internal socket write with no data escaping. used to send telnet stuff."""
        writer = self._socket[1]
        self.logger.debug("SEND %r", data)
        async with self._write_lock:
            writer.write(data)
            await writer.drain()

    async def telnet_send_option(self, action, option):
        """Send DO, DONT, WILL, WONT."""
        await self._internal_raw_write(IAC + action + option)

    async def telnet_send_options(self, action_options):
        """Send DO, DONT, WILL, WONT."""
        data = []
        for action, option in action_options:
            data += IAC, action, option
        await self._internal_raw_write(b"".join(data))

    async def rfc2217_send_subnegotiation(self, option, value=b""):
        """Subnegotiation of RFC2217 parameters."""
        value = value.replace(IAC, IAC_DOUBLED)
        await self._internal_raw_write(
            IAC + SB + COM_PORT_OPTION + option + value + IAC + SE
        )

    async def rfc2217_send_purge(self, value):
        """\
        Send purge request to the remote.
        (PURGE_RECEIVE_BUFFER / PURGE_TRANSMIT_BUFFER / PURGE_BOTH_BUFFERS)
        """
        item = self._rfc2217_options["purge"]
        await item.set(value)  # transmit desired purge type
        # wait for acknowledge from the server
        await asyncio.wait_for(item.wait(), self._network_timeout)

    async def rfc2217_set_control(self, value):
        """transmit change of control line to remote"""
        item = self._rfc2217_options["control"]
        await item.set(value)  # transmit desired control type
        if self._ignore_set_control_answer:
            # answers are ignored when option is set. compatibility mode for
            # servers that answer, but not the expected one... (or no answer
            # at all) i.e. sredird
            # this helps getting the unit tests passed
            await asyncio.sleep(0.1)
        else:
            # wait for acknowledge from the server
            await asyncio.wait_for(item.wait(), self._network_timeout)

    def rfc2217_flow_server_ready(self):
        """\
        check if server is ready to receive data. block for some time when
        not.
        """
        # ~ if self._remote_suspend_flow:
        # ~     wait---

    async def get_modem_state(self):
        """\
        get last modem state (cached value. If value is "old", request a new
        one. This cache helps that we don't issue to many requests when e.g. all
        status lines, one after the other is queried by the user (CTS, DSR
        etc.)
        """
        # active modem state polling enabled? is the value fresh enough?
        if self._poll_modem_state and self._modemstate_timeout.expired():
            self.logger.debug("polling modem state")
            # when it is older, request an update
            await self.rfc2217_send_subnegotiation(NOTIFY_MODEMSTATE)
            timeout = Timeout(self._network_timeout)
            while not timeout.expired():
                await asyncio.sleep(0.05)  # prevent 100% CPU load
                # when expiration time is updated, it means that there is a new
                # value
                if not self._modemstate_timeout.expired():
                    break
            else:
                self.logger.warning("poll for modem state failed")
            # even when there is a timeout, do not generate an error just
            # return the last known value. this way we can support buggy
            # servers that do not respond to polls, but send automatic
            # updates.
        if self._modemstate is not None:
            self.logger.debug("using cached modem state")
            return self._modemstate
        else:
            # never received a notification from the server
            raise SerialException("remote sends no NOTIFY_MODEMSTATE")
