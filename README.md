# serialio

![Pypi version][pypi]

A python concurrency agnostic serial line library.

Helpful when handling with instrumentation which work over serial line and implement
simple REQ-REP communication protocols (example:
[SCPI](https://en.m.wikipedia.org/wiki/Standard_Commands_for_Programmable_Instruments)).

Besides local serial line, serialio also supports serial line over RFC2217 protocol,
raw TCP socket and [tango](https://tango-controls.org).

As far as RFC2217 is concerned, it should be compatible with:

* ser2net bridge with telnet(RFC2217) and raw configurations
* gserial[ser2tcp] bridge (RFC2217)

As far as tango is concerned, it should be compatible with the tango classes:

* [Serial](https://sourceforge.net/p/tango-ds/code/HEAD/tree/DeviceClasses/Communication/SerialLine/)


Base implementation written in asyncio with support for different concurrency models:

* asyncio
* classic blocking API
* future based API

Here is a summary of what is forseen and what is implemented

| Concurrency   | Local  | RFC2217 | Raw TCP | [Tango](http://www.esrf.eu/computing/cs/tango/tango_doc/ds_doc/tango-ds/index.html) |
| ------------- |:------:|:-------:|:-------:|:-----------------------------------------------------------------------------------:|
| asyncio       |   Y    |    Y    |    Y    |                                        Y                                            |
| classic sync  |   Y    |    Y    |    Y    |                                        Y                                            |
| conc. futures |   Y    |    Y    |    Y    |                                        Y                                            |


## Installation

From within your favourite python environment:

```console
pip install serialio
```

## Usage

### asyncio

```python
import asyncio
import serialio.aio.tcp

async def main():
    sl = serialio.serial_for_url("serial-tcp://lab1.acme.org:5000")
    # or the equivalent:
    # sl = serialio.aio.tcp.Serial("lab1.acme.org", 5000)

    await sl.open()

    # Assuming a SCPI complient on the other end we can ask for:
    reply = await sl.write_readline(b"*IDN?\n")
    print(reply)
    await sl.close()

asyncio.run(main())
```

*local serial line*

```python
import serialio.aio.posix

sl = serialio.aio.posix.Serial("/dev/ttyS0")

# or the equivalent

sl = serialio.serial_for_url("serial:///dev/ttyS0")
```

*raw TCP socket*

```python
import serialio.aio.tcp

sl = serialio.aio.tcp.Serial("lab1.acme.org:5000")

# or the equivalent

sl = serialio.serial_for_url("serial+tcp://lab1.acme.org:5000")
```

*RFC2217 (telnet)*

```python
import serialio.aio.rfc2217

sl = serialio.aio.rfc2217.Serial("lab1.acme.org:5000")

# or the equivalent

sl = serialio.serial_for_url("rfc2217://lab1.acme.org:5000")
```

*Tango*

(needs a `pip install serialio[tango]` installation)

```python
import serialio.aio.tango
sl = serialio.aio.tango.Serial("lab/01/serial-01")

# or the equivalent

sl = serialio.serial_for_url("serial+tango://lab/01/serial-01")
```

### classic

```python

from serialio.aio.tcp import Serial

sl = Serial("lab1.acme.org", 5000)
reply = sl.write_readline(b"*IDN?\n")
print(reply)
```

### concurrent.futures

```python
from serialio.sio.tcp import Serial

sl = Serial("lab1.acme.org", 5000, resolve_futures=False)
reply = sl.write_readline(b"*IDN?\n").result()
print(reply)
```

## API differences with [serial](https://github.com/pyserial/pyserial)

* coroutine based API
* `open()` coroutine must be called explicitly before using the serial line
* setting of parameters done through functions instead of properties (ie:
  `await ser_line.set_XXX(value)` instead of `ser_line.XXX = value`
  (ex: `await ser_line.set_baudrate()`))
* custom `eol` character (serial is fixed to `b"\n"`)
* included REQ/REP atomic functions (`write_read()` family)

## Features

The main goal of a serialio Serial object is to facilitate communication
with instruments connected to a serial line.

The most frequent cases include instruments which expect a REQ/REP
semantics with ASCII protocols like SCPI. In these cases most commands
translate in small packets being exchanged between the host and the
instrument.

### REQ-REP semantics

Many instruments out there have a Request-Reply protocol. A serialio Serial
provides helpful `write_read` family of methods which simplify communication
with these instruments.

### Custom EOL

In line based protocols, sometimes people decide `\n` is not a good EOL character.
A serialio can be customized with a different EOL character.
For example, the [XIA-PFCU](https://github.com/tiagocoutinho/xia-pfcu) always
replies with `;\r\n`, so we could configure it using the following snippet:

```python
sl = serialio.serial_for_url("serial:///dev/ttyS0", eol=b";\r\n")
await sl.open()
```

The initial EOL character can be overwritten in any of the `readline` methods.
Example:

```python
await sl.write_readline(b"*IDN?\n", eol=b"\r")
```

### Streams

TODO: Write this chapter

[pypi]: https://img.shields.io/pypi/pyversions/serialio.svg
