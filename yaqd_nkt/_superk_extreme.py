__all__ = ["SuperkExtreme"]

import asyncio

from yaqd_core import HasPosition, IsDaemon, IsDiscrete, UsesSerial, UsesUart

from ._interbus import InterbusDevice, InterbusError


class SuperkExtreme(UsesUart, UsesSerial, IsDiscrete, HasPosition, IsDaemon):
    _kind = "superk-extreme"

    def __init__(self, name, config, config_filepath):
        super().__init__(name, config, config_filepath)
        self._laser_address = 15  # SuperK Extreme laser address
        self._ib = InterbusDevice(self._config["serial_port"])
    
    def _set_position(self, position):
        self._ib.set_reg(self._laser_address, 0x30, position, "u8")
    
    def get_power(self):
        power = self._ib.get_reg(self._laser_address, 0x37, "u16") / 10
        return power
    
    def set_power(self, power: int):
        int_power = power*10  # Power command is in permille ‰
        self._ib.set_reg(self._laser_address, 0x37, int_power, "u16")

    async def update_state(self):
        """Continually monitor and update the current laser emission state."""
        while True:
            # Perform any updates to internal state
            try:
                position = self._ib.get_reg(self._laser_address, 0x30, "u8")
                self._state["position"] = position
                for k, v in self._position_identifiers.items():
                    if v == position:
                        
                        self._state["position_identifier"] = k

            except InterbusError as e:
                self.logger.error(e)
                await asyncio.sleep(0.5)
                continue


            # There must be at least one `await` in this loop
            # This one waits for something to trigger the "busy" state
            # (Setting `self._busy = True)
            # Otherwise, you can simply `await asyncio.sleep(0.01)`

            await asyncio.sleep(1)

    def direct_serial_write(self, message: bytes):
        raise NotImplementedError
