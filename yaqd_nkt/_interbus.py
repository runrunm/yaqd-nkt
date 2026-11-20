"""
Minimal NKT Interbus implementation.

Dependencies:
    pip install pyserial

Protocol implementation based on pylablib.devices.NKT.interbus.
"""

import struct
import threading
from collections import namedtuple

try:
    import serial
except ImportError:
    serial = None

class InterbusError(Exception):
    pass

Telegram = namedtuple("Telegram", "dest src typ payload")

class InterbusDevice:

    SOURCE = 0x40

    ESCAPE = {0x0A, 0x0D, 0x5E}
    ESCAPED = {0x4A, 0x4D, 0x9E}

    TYPES = {
        0: "faulty_telegram",
        1: "crc_error",
        2: "busy",
        3: "ack",
        4: "read",
        5: "write",
        8: "reply",
    }

    DTYPES = {
        "u8": "B",
        "u16": "<H",
        "u32": "<I",
        "i8": "b",
        "i16": "<h",
        "i32": "<i",
    }

    def __init__(self, port, baudrate=115200, timeout=1.0):
        if serial is None:
            raise ImportError("Install pyserial: pip install pyserial")

        self.ser = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)
        self.lock = threading.Lock()

    def close(self):
        self.ser.close()

    # CRC-16 (polynomial 0x1021, initial value 0)
    @staticmethod
    def crc(data):
        crc = 0
        for byte in data:
            crc ^= byte << 8
            for _ in range(8):
                if crc & 0x8000:
                    crc = (crc << 1) ^ 0x1021
                else:
                    crc <<= 1
                crc &= 0xFFFF
        return crc

    # Byte escaping
    @classmethod
    def escape(cls, data):
        result = bytearray([0x0D])
        for byte in data:
            if byte in cls.ESCAPE:
                result.extend([0x5E, byte + 0x40])
            else:
                result.append(byte)

        result.append(0x0A)

        return bytes(result)

    @classmethod
    def unescape(cls, data):
        result = bytearray()
        escaped = False
        
        for byte in data:
            if escaped:
                if byte not in cls.ESCAPED:
                    raise InterbusError(f"Invalid escape byte: 0x{byte:02X}")
                result.append(byte - 0x40)
                escaped = False
            elif byte == 0x5E:
                escaped = True
            else:
                result.append(byte)

        if escaped:
            raise InterbusError("Incomplete escape sequence")

        return bytes(result)

    # Telegram handling
    def build_telegram(self, dest, src, typ, payload=b""):
        msg = struct.pack("BBB", dest, src, typ) + bytes(payload)

        crc = self.crc(msg)
        msg += struct.pack(">H", crc)

        return self.escape(msg)

    def send_telegram(self, dest, src, typ, payload=b""):
        msg = self.build_telegram(dest, src, typ, payload)
        self.ser.write(msg)
        self.ser.flush()

    def receive_telegram(self):
        start = self.ser.read(1)

        if start != b"\x0D":
            raise InterbusError(f"Invalid telegram start: {start!r}")

        data = self.ser.readline()

        if not data.endswith(b"\x0A"):
            raise InterbusError("Telegram timeout or missing terminator")

        data = self.unescape(data[:-1])

        if len(data) < 5:
            raise InterbusError("Telegram too short")

        expected_crc = self.crc(data[:-2])
        received_crc = struct.unpack(">H", data[-2:])[0]

        if expected_crc != received_crc:
            raise InterbusError(
                f"CRC error: expected {expected_crc:04X}, "
                f"received {received_crc:04X}"
            )

        return Telegram(
            dest=data[0],
            src=data[1],
            typ=data[2],
            payload=data[3:-2]
        )

    def check_telegram(self, telegram, src=None, dest=None, typ=None):

        if src is not None and telegram.src != src:
            raise InterbusError("Unexpected source address")

        if dest is not None and telegram.dest != dest:
            raise InterbusError("Unexpected destination address")

        if telegram.typ in (0, 1, 2):
            raise InterbusError(f"Device error: {self.TYPES[telegram.typ]}")

        if typ is not None and telegram.typ != typ:
            raise InterbusError(f"Unexpected telegram type: {telegram.typ}")

    # Register access
    def get_reg(self, dest, reg, dtype="raw", array="auto"):
        with self.lock:
            self.send_telegram(dest, self.SOURCE, 4, struct.pack("B", reg))

            telegram = self.receive_telegram()

        self.check_telegram(telegram, src=dest, dest=self.SOURCE, typ=8)

        payload = telegram.payload

        if not payload or payload[0] != reg:
            raise InterbusError("Invalid register response")

        value = payload[1:]

        if dtype == "raw":
            return value

        if dtype == "str":
            return value.decode("ascii", errors="replace")

        if dtype not in self.DTYPES:
            raise ValueError(f"Unknown dtype: {dtype}")

        fmt = self.DTYPES[dtype]
        size = struct.calcsize(fmt)

        if array == "auto":
            array = len(value) > size

        if array:
            if len(value) % size:
                raise InterbusError("Invalid register array length")

            return [
                struct.unpack(fmt, value[i:i + size])[0]
                for i in range(0, len(value), size)
            ]

        if len(value) != size:
            raise InterbusError("Invalid register length")

        return struct.unpack(fmt, value)[0]

    def set_reg(self, dest, reg, value, dtype="raw", array="auto", echo=True):
        if dtype in ("raw", "str"):
            data = value.encode("ascii") if isinstance(value, str) else bytes(value)
        else:
            if dtype not in self.DTYPES:
                raise ValueError(f"Unknown dtype: {dtype}")
            fmt = self.DTYPES[dtype]
            # size = struct.calcsize(fmt)
            if array == "auto":
                array = isinstance(value, (list, tuple))
            if array:
                data = b"".join(struct.pack(fmt, int(v)) for v in value)
            else:
                data = struct.pack(fmt, int(value))

        with self.lock:
            self.send_telegram(dest, self.SOURCE, 5, struct.pack("B", reg) + data)
            telegram = self.receive_telegram()

        self.check_telegram(telegram, src=dest, dest=self.SOURCE, typ=3)

        if not telegram.payload or telegram.payload[0] != reg:
            raise InterbusError("Invalid write acknowledgement")

        if echo:
            return self.get_reg(dest, reg, dtype=dtype, array=array)

    # Device discovery
    def scan_devices(self, dests=range(1, 49), timeout=0.05):
        detected = {}
        old_timeout = self.ser.timeout
        try:
            self.ser.timeout = timeout
            with self.lock:
                for dest in dests:
                    try:
                        self.send_telegram(dest, self.SOURCE, 4, b"\x61")
                        telegram = self.receive_telegram()
                        self.check_telegram(telegram, src=dest, dest=self.SOURCE, typ=8)
                        if (len(telegram.payload) >= 2 and telegram.payload[0] == 0x61):
                            detected[dest] = telegram.payload[1]
                    except (InterbusError, IndexError):
                        continue

        finally:
            self.ser.timeout = old_timeout

        return detected


if __name__ == "__main__":
    ib = InterbusDevice("COM3")
    device_type = ib.get_reg(15, 0x61, "u8")
    print("Device type:", hex(device_type))

    # Read serial number
    serial_number = ib.get_reg(15, 0x65, "str")
    print("Serial number:", serial_number)

    # Read inlet temperature (0.1 °C units)
    temperature = ib.get_reg(15, 0x11, "i16") / 10
    print("Temperature:", temperature, "°C")

    # Read output power (0.1% units)
    power = ib.get_reg(15, 0x37, "u16") / 10
    print("Power:", power, "%")

    ib.set_reg(15, 0x37, 170, "u16")

    ib.set_reg(15, 0x30, 0, "u8")