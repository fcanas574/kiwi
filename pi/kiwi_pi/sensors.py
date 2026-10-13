"""Sensores de presencia por posición, con antirrebote y notificación de cambios.

Cada posición reporta ocupada (True), vacía (False) o desconocida (None). Cualquier sensor
digital sirve: IR de obstáculo, barrera IR, microswitch o reed + imán. Para otros (peso,
distancia, RFID) basta con escribir un driver con `read()`.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from .config import Config


class SensorDriver:
    name = 'none'

    def __init__(self, cfg: Config, count: int):
        self.count = count

    def read(self) -> list[bool | None]:
        return [None] * self.count

    def close(self) -> None:
        pass


class MockSensors(SensorDriver):
    """Estados simulados; se cambian desde el panel de diagnóstico."""
    name = 'mock'

    def __init__(self, cfg: Config, count: int):
        super().__init__(cfg, count)
        self.values: list[bool | None] = [False] * count

    def read(self) -> list[bool | None]:
        return list(self.values)


class GpioSensors(SensorDriver):
    name = 'gpio'

    def __init__(self, cfg: Config, count: int):
        super().__init__(cfg, count)
        from gpiozero import DigitalInputDevice
        section = cfg.section('sensors')
        pins = section['gpio_pins']
        if len(pins) < count:
            raise ValueError(f'sensors.gpio_pins necesita {count} pines.')
        pull = section.get('pull', 'up')
        occupied_high = section.get('occupied_when', 'low') == 'high'
        if pull == 'none':
            self.devices = [DigitalInputDevice(p, pull_up=None, active_state=occupied_high) for p in pins[:count]]
            self.invert = False
        else:
            # Con pull-up el dispositivo está "activo" en nivel bajo; con pull-down, en nivel alto.
            pull_up = pull == 'up'
            self.devices = [DigitalInputDevice(p, pull_up=pull_up) for p in pins[:count]]
            self.invert = pull_up == occupied_high

    def read(self) -> list[bool | None]:
        return [bool(d.is_active) != self.invert for d in self.devices]

    def close(self) -> None:
        for device in self.devices:
            device.close()


def make_sensor_driver(cfg: Config, count: int) -> SensorDriver:
    driver = cfg.section('sensors').get('driver', 'mock')
    classes = {'none': SensorDriver, 'mock': MockSensors, 'gpio': GpioSensors}
    try:
        return classes[driver](cfg, count)
    except Exception as exc:
        print(f'Sensores {driver} no disponibles ({exc}); se usa la simulación.')
        return MockSensors(cfg, count)


class SensorMonitor:
    def __init__(self, cfg: Config, on_change: Callable[[int, bool | None, bool | None], Awaitable[None]]):
        section = cfg.section('sensors')
        self.ids = [s.id for s in cfg.slots]
        self.driver = make_sensor_driver(cfg, len(self.ids))
        self.debounce = float(section.get('debounce_ms', 250)) / 1000
        self.poll = float(section.get('poll_ms', 50)) / 1000
        initial = self.driver.read()
        self.state: dict[int, bool | None] = dict(zip(self.ids, initial))
        self._pending: dict[int, tuple[bool | None, float]] = {}
        self._on_change = on_change

    @property
    def driver_name(self) -> str:
        return self.driver.name

    @property
    def enabled(self) -> bool:
        return self.driver.name != 'none'

    def simulate(self, slot_id: int, occupied: bool) -> None:
        if not isinstance(self.driver, MockSensors):
            raise RuntimeError('Solo disponible con sensors.driver: mock')
        self.driver.values[self.ids.index(slot_id)] = occupied

    async def tick(self) -> None:
        now = time.monotonic()
        for slot_id, value in zip(self.ids, self.driver.read()):
            if value == self.state[slot_id]:
                self._pending.pop(slot_id, None)
                continue
            pending = self._pending.get(slot_id)
            if pending is None or pending[0] != value:
                self._pending[slot_id] = (value, now)
            elif now - pending[1] >= self.debounce:
                previous = self.state[slot_id]
                self.state[slot_id] = value
                self._pending.pop(slot_id, None)
                await self._on_change(slot_id, previous, value)

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:
                print('Sensores:', exc)
                await asyncio.sleep(1)
            await asyncio.sleep(self.poll)

    def close(self) -> None:
        self.driver.close()
