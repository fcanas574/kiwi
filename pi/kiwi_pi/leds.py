"""Señalización luminosa por posición.

El controlador guarda un modo lógico por posición (off, guide, ok, wrong, test) y un bucle
de animación calcula el color de cada cuadro. Los drivers solo escriben colores, así el
parpadeo funciona igual con LEDs simples, tiras WS2812 o un microcontrolador por USB.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable

from .config import Config

RGB = tuple[int, int, int]
OFF: RGB = (0, 0, 0)
MODES = ('off', 'guide', 'ok', 'wrong', 'test')


def hex_to_rgb(value: str) -> RGB:
    value = value.lstrip('#')
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


class LedDriver:
    name = 'mock'
    supports_color = True

    def __init__(self, cfg: Config, count: int):
        self.count = count

    def write(self, index: int, color: RGB) -> None:
        pass

    def show(self) -> None:
        pass

    def close(self) -> None:
        for i in range(self.count):
            self.write(i, OFF)
        self.show()


class GpioLeds(LedDriver):
    """Un LED por posición (con resistencia de 330 Ω, o un transistor para barras de LED)."""
    name = 'gpio'
    supports_color = False

    def __init__(self, cfg: Config, count: int):
        super().__init__(cfg, count)
        from gpiozero import LED
        section = cfg.section('leds')
        pins = section['gpio_pins']
        if len(pins) < count:
            raise ValueError(f'leds.gpio_pins necesita {count} pines.')
        self.leds = [LED(pin, active_high=bool(section.get('gpio_active_high', True))) for pin in pins[:count]]

    def write(self, index: int, color: RGB) -> None:
        self.leds[index].value = 1 if any(color) else 0

    def close(self) -> None:
        super().close()
        for led in self.leds:
            led.close()


class Ws281xLeds(LedDriver):
    """Tira WS2812B: `pixels_per_slot` píxeles consecutivos por posición. Requiere sudo."""
    name = 'ws281x'

    def __init__(self, cfg: Config, count: int):
        super().__init__(cfg, count)
        from rpi_ws281x import PixelStrip, Color
        section = cfg.section('leds')
        self.per_slot = int(section.get('ws281x_pixels_per_slot', 8))
        brightness = int(255 * float(section.get('ws281x_brightness', 0.4)))
        self.Color = Color
        self.strip = PixelStrip(self.per_slot * count, int(section.get('ws281x_pin', 18)), brightness=brightness)
        self.strip.begin()

    def write(self, index: int, color: RGB) -> None:
        value = self.Color(*color)
        for pixel in range(index * self.per_slot, (index + 1) * self.per_slot):
            self.strip.setPixelColor(pixel, value)

    def show(self) -> None:
        self.strip.show()


class SerialLeds(LedDriver):
    """ESP32/Arduino por USB. Protocolo: `<posición 1..n> <RRGGBB>\\n`; ver firmware/kiwi_leds."""
    name = 'serial'

    def __init__(self, cfg: Config, count: int):
        super().__init__(cfg, count)
        import serial
        section = cfg.section('leds')
        self.port = serial.Serial(section.get('serial_port', '/dev/ttyUSB0'),
                                  int(section.get('serial_baud', 115200)), timeout=0.1)
        time.sleep(2)  # muchas placas se reinician al abrir el puerto
        self.pending: list[bytes] = []

    def write(self, index: int, color: RGB) -> None:
        self.pending.append(f'{index + 1} {color[0]:02X}{color[1]:02X}{color[2]:02X}\n'.encode())

    def show(self) -> None:
        if self.pending:
            self.port.write(b''.join(self.pending))
            self.pending.clear()

    def close(self) -> None:
        super().close()
        self.port.close()


def make_driver(cfg: Config, count: int) -> LedDriver:
    driver = cfg.section('leds').get('driver', 'mock')
    classes = {'mock': LedDriver, 'gpio': GpioLeds, 'ws281x': Ws281xLeds, 'serial': SerialLeds}
    try:
        return classes[driver](cfg, count)
    except Exception as exc:
        print(f'LEDs {driver} no disponibles ({exc}); se usa la simulación.')
        return LedDriver(cfg, count)


class LedController:
    FRAME = 0.05

    def __init__(self, cfg: Config, on_change: Callable[[], None] = lambda: None):
        self.cfg = cfg
        section = cfg.section('leds')
        colors = section.get('colors', {})
        self.colors = {mode: hex_to_rgb(colors.get(mode, default)) for mode, default in
                       (('guide', '#006DFF'), ('ok', '#31E083'), ('wrong', '#FF3B1F'), ('test', '#FFFFFF'))}
        self.ids = [s.id for s in cfg.slots]
        self.driver = make_driver(cfg, len(self.ids))
        self.modes: dict[int, str] = {slot_id: 'off' for slot_id in self.ids}
        self._since: dict[int, float] = {slot_id: 0.0 for slot_id in self.ids}
        self._shown: list[RGB | None] = [None] * len(self.ids)
        self._on_change = on_change
        self._task: asyncio.Task | None = None

    @property
    def driver_name(self) -> str:
        return self.driver.name

    def set(self, slot_id: int, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(mode)
        if self.modes.get(slot_id) != mode:
            self.modes[slot_id] = mode
            self._since[slot_id] = time.monotonic()
            self._on_change()

    def all_off(self) -> None:
        for slot_id in self.ids:
            self.set(slot_id, 'off')

    def color_for(self, slot_id: int, now: float) -> RGB:
        mode = self.modes[slot_id]
        if mode == 'off':
            return OFF
        elapsed = now - self._since[slot_id]
        color = self.colors[mode]
        if mode == 'guide':   # encendido fijo con un breve destello al empezar
            return color if elapsed > 0.9 or int(elapsed / 0.15) % 2 == 0 else OFF
        if mode == 'wrong':   # parpadeo rápido
            return color if int(elapsed / 0.2) % 2 == 0 else OFF
        if mode == 'ok' and not self.driver.supports_color:   # sin color: tres destellos y fijo
            return color if elapsed > 1.2 or int(elapsed / 0.2) % 2 == 0 else OFF
        return color

    def render(self) -> None:
        now = time.monotonic()
        changed = False
        for index, slot_id in enumerate(self.ids):
            color = self.color_for(slot_id, now)
            if self._shown[index] != color:
                self.driver.write(index, color)
                self._shown[index] = color
                changed = True
        if changed:
            self.driver.show()

    async def run(self) -> None:
        while True:
            try:
                self.render()
            except Exception as exc:
                print('LEDs:', exc)
                await asyncio.sleep(1)
            await asyncio.sleep(self.FRAME)

    async def self_test(self) -> None:
        """Enciende cada posición por separado (sección 10.3 del documento del reto)."""
        previous = dict(self.modes)
        self.all_off()
        for slot_id in self.ids:
            self.set(slot_id, 'test')
            await asyncio.sleep(0.5)
            self.set(slot_id, 'off')
        for slot_id, mode in previous.items():
            self.set(slot_id, mode)

    def close(self) -> None:
        self.driver.close()

    def snapshot(self) -> dict[int, str]:
        return dict(self.modes)
