__all__ = ["SuperkExtreme"]

import asyncio

from yaqd_core import HasLimits, HasPosition, IsDaemon, IsDiscrete, UsesSerial, UsesUart

from ._interbus import InterbusDevice, InterbusError


class SuperkExtreme(UsesUart, UsesSerial, IsDiscrete, HasLimits, HasPosition, IsDaemon):
    _kind = "superk-extreme"

    def __init__(self, name, config, config_filepath):
        super().__init__(name, config, config_filepath)
        self._laser_address = 15  # SuperK Extreme laser address
        self._ib = InterbusDevice(self._config["serial_port"])
    
    def _set_position(self):
        pass

    async def update_state(self):
        """Continually monitor and update the current daemon state."""
        # If there is no state to monitor continuously, delete this function
        while True:
            # Perform any updates to internal state
            try:
                emission = True if self._ib.get_reg(self._address, 0x30, "u16") == 3 else None
            except InterbusError as e:
                self.logger.error(e)
                await asyncio.sleep(0.5)
                continue
            self._state["emission"] = emission
            # There must be at least one `await` in this loop
            # This one waits for something to trigger the "busy" state
            # (Setting `self._busy = True)
            # Otherwise, you can simply `await asyncio.sleep(0.01)`

            await asyncio.sleep(1)
