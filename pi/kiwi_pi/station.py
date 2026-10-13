"""Lógica de la estación: guía hacia la posición correcta y verifica la devolución con los sensores.

Fases de la guía:
  idle        sin herramienta pendiente
  guiding     LED azul en la posición asignada; se espera que la ocupen
  wrong_slot  se ocupó otra posición vacía; parpadea en rojo hasta que la liberen
  returned    la posición correcta se ocupó; LED verde y vuelta al inicio
  timeout     nadie la devolvió a tiempo; se apagan las luces
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from .config import Config, Slot
from .leds import LedController
from .recognizer import Health, Recognizer
from .sensors import SensorMonitor


class EventBus:
    def __init__(self):
        self._queues: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=20)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._queues.discard(queue)

    def publish(self, message: Any) -> None:
        for queue in list(self._queues):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(message)


class Station:
    def __init__(self, cfg: Config, recognizer: Recognizer, camera_name: str = 'none'):
        self.cfg = cfg
        self.recognizer = recognizer
        self.camera_name = camera_name
        self.bus = EventBus()
        self.leds = LedController(cfg, on_change=self.notify)
        self.sensors = SensorMonitor(cfg, on_change=self.on_sensor)
        guide = cfg.section('guide')
        self.guide_timeout = float(guide.get('guide_timeout_seconds', 120))
        self.auto_reset = float(guide.get('auto_reset_seconds', 5))
        self.ok_seconds = float(cfg.section('leds').get('ok_seconds', 4))
        self.health = Health(False, 'Comprobando…')
        self.phase = 'idle'
        self.target: Slot | None = None
        self.wrong: int | None = None
        self.wrong_slots: set[int] = set()
        self.recognition_version = 0
        self.started_at = 0.0
        self.test_mode = False
        self.target_was_occupied = False
        self._timer: asyncio.Task | None = None
        self._tasks: list[asyncio.Task] = []
        self._notify_scheduled = False

    # ---------- ciclo de vida ----------
    async def start(self) -> None:
        self._tasks = [asyncio.create_task(self.leds.run()), asyncio.create_task(self._health_loop())]
        if self.sensors.enabled:
            self._tasks.append(asyncio.create_task(self.sensors.run()))

    async def stop(self) -> None:
        for task in self._tasks + ([self._timer] if self._timer else []):
            task.cancel()
        self.leds.all_off()
        self.leds.render()
        self.leds.close()
        self.sensors.close()
        await self.recognizer.close()

    async def _health_loop(self) -> None:
        interval = float(self.cfg.section('recognizer').get('health_interval_seconds', 15))
        while True:
            await self.refresh_health()
            await asyncio.sleep(interval)

    async def refresh_health(self) -> None:
        health = await self.recognizer.health()
        if health != self.health:
            self.health = health
            self.notify()

    # ---------- eventos ----------
    def notify(self) -> None:
        """Agrupa varios cambios en un solo mensaje por vuelta del bucle."""
        if self._notify_scheduled:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._notify_scheduled = True

        def flush():
            self._notify_scheduled = False
            self.bus.publish(self.snapshot())
        loop.call_soon(flush)

    def snapshot(self) -> dict[str, Any]:
        leds = self.leds.snapshot()
        remaining = None
        if self.phase in ('guiding', 'wrong_slot'):
            remaining = max(0, round(self.guide_timeout - (time.monotonic() - self.started_at)))
        return {
            'api': {'online': self.health.online, 'detail': self.health.detail, 'mode': self.health.mode,
                    'calibrated': self.health.calibrated, 'url': self.recognizer.url},
            'drivers': {'recognizer': self.recognizer.name, 'camera': self.camera_name,
                        'leds': self.leds.driver_name, 'sensors': self.sensors.driver_name},
            'columns': int(self.cfg.section('station').get('shelf_columns', 3)),
            'slots': [{**s.as_dict(), 'occupied': self.sensors.state.get(s.id), 'led': leds[s.id]}
                      for s in self.cfg.slots],
            'guide': {'phase': self.phase, 'target': self.target.as_dict() if self.target else None,
                      'wrong': self.wrong, 'test_mode': self.test_mode, 'remaining': remaining,
                      'verify': self.sensors.enabled, 'target_was_occupied': self.target_was_occupied},
        }

    # ---------- guía ----------
    def _set_timer(self, delay: float, callback) -> None:
        if self._timer:
            self._timer.cancel()

        async def run():
            await asyncio.sleep(delay)
            self._timer = None
            callback()
        self._timer = asyncio.create_task(run())

    def begin_guide(self, slot: Slot, test_mode: bool = False) -> None:
        self.leds.all_off()
        self.target = slot
        self.wrong = None
        self.wrong_slots.clear()
        self.test_mode = test_mode
        self.phase = 'guiding'
        self.started_at = time.monotonic()
        self.target_was_occupied = self.sensors.state.get(slot.id) is True
        self.leds.set(slot.id, 'guide')
        self._set_timer(self.guide_timeout, self._on_timeout)
        self.notify()

    def cancel_guide(self) -> None:
        self.recognition_version += 1
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self.leds.all_off()
        self.phase = 'idle'
        self.target = None
        self.wrong = None
        self.wrong_slots.clear()
        self.test_mode = False
        self.notify()

    def _on_timeout(self) -> None:
        if self.phase in ('guiding', 'wrong_slot'):
            self.leds.all_off()
            self.phase = 'timeout'
            self.notify()

    def _on_returned(self) -> None:
        self.phase = 'returned'
        self.wrong = None
        self.wrong_slots.clear()
        self.leds.all_off()
        self.leds.set(self.target.id, 'ok')
        self._set_timer(max(self.ok_seconds, self.auto_reset), self.cancel_guide)
        if self.ok_seconds < self.auto_reset:
            asyncio.get_running_loop().call_later(self.ok_seconds, self._ok_off, self.target.id)
        self.notify()

    def _ok_off(self, slot_id: int) -> None:
        if self.leds.modes.get(slot_id) == 'ok':
            self.leds.set(slot_id, 'off')

    async def on_sensor(self, slot_id: int, previous: bool | None, occupied: bool | None) -> None:
        self.notify()
        if self.phase not in ('guiding', 'wrong_slot') or self.target is None:
            return
        if slot_id == self.target.id:
            if occupied:
                self._on_returned()
            elif previous:   # alguien la retiró: deja de considerarse ocupada antes de devolverla
                self.target_was_occupied = False
            return
        if occupied and previous is False:
            self.wrong_slots.add(slot_id)
            self.wrong = slot_id
            self.phase = 'wrong_slot'
            self.leds.set(slot_id, 'wrong')
        elif not occupied and slot_id in self.wrong_slots:
            self.leds.set(slot_id, 'off')
            self.wrong_slots.remove(slot_id)
            self.wrong = min(self.wrong_slots) if self.wrong_slots else None
            self.phase = 'wrong_slot' if self.wrong_slots else 'guiding'
        self.notify()

    def simulate_sensor(self, slot_id: int, occupied: bool) -> None:
        self.sensors.simulate(slot_id, occupied)

    async def led_self_test(self) -> None:
        await self.leds.self_test()
