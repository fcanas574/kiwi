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
        self.sensors = SensorMonitor(cfg, on_change=self.on_sensor, on_batch=self._on_sensor_batch)
        guide = cfg.section('guide')
        self.guide_timeout = float(guide.get('guide_timeout_seconds', 120))
        self.auto_reset = float(guide.get('auto_reset_seconds', 5))
        self.ok_seconds = float(cfg.section('leds').get('ok_seconds', 4))
        self.health = Health(False, 'Comprobando…')
        self.phase = 'idle'
        self.targets: dict[int, Slot] = {}
        self.pending: set[int] = set()
        self.confirmed: set[int] = set()
        self.blocked: set[int] = set()
        self.operation_id: str | None = None
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
            'guide': {'operation_id': self.operation_id, 'targets': [s.as_dict() for s in self.targets.values()],
                      'pending': sorted(self.pending), 'confirmed': sorted(self.confirmed),
                      'blocked': sorted(self.blocked), 'wrong_slots': sorted(self.wrong_slots),
                      'phase': self.phase, 'target': self.target.as_dict() if self.target else None,
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
        # Compatibility for the old internal single-slot caller during migration.
        self.begin_guides([slot], 'single', test_mode)

    def begin_guides(self, slots: list[Slot], operation_id: str, test_mode: bool = False) -> None:
        self.cancel_guide()
        self.targets = {slot.id: slot for slot in slots}
        if not self.targets:
            return
        self.operation_id = operation_id
        self.pending = set(self.targets)
        self.confirmed.clear()
        self.blocked = {slot for slot in self.targets if self.sensors.state.get(slot) is True}
        self.target = next(iter(self.targets.values())) if len(self.targets) == 1 else None
        self.target_was_occupied = bool(self.blocked)
        self.test_mode = test_mode
        self.phase = 'guiding'
        self.started_at = time.monotonic()
        for slot in self.targets:
            self.leds.set(slot, 'guide')
        self._set_timer(self.guide_timeout, self._on_timeout)
        self.notify()

    def cancel_guide(self) -> None:
        self.recognition_version += 1
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self.leds.all_off()
        self.phase = 'idle'
        self.targets.clear()
        self.pending.clear()
        self.confirmed.clear()
        self.blocked.clear()
        self.operation_id = None
        self.target = None
        self.target_was_occupied = False
        self.wrong = None
        self.wrong_slots.clear()
        self.test_mode = False
        self.notify()

    def _on_timeout(self) -> None:
        if self.phase in ('guiding', 'wrong_slot'):
            self.leds.all_off()
            self.phase = 'timeout'
            self.notify()

    def _update_progress(self) -> None:
        self.wrong = min(self.wrong_slots) if self.wrong_slots else None
        self.target_was_occupied = bool(self.blocked)
        if not self.pending and not self.wrong_slots and not self.sensors.processing_batch:
            self.phase = 'returned'
            self._set_timer(max(self.ok_seconds, self.auto_reset), self.cancel_guide)
        else:
            self.phase = 'wrong_slot' if self.wrong_slots else 'guiding'
        self.notify()

    def _on_sensor_batch(self) -> None:
        if self.phase in ('guiding', 'wrong_slot'):
            self._update_progress()

    async def on_sensor(self, slot_id: int, previous: bool | None, occupied: bool | None) -> None:
        self.notify()
        if not self.sensors.enabled or self.phase not in ('guiding', 'wrong_slot'):
            return
        if slot_id in self.targets:
            if occupied is False:
                self.blocked.discard(slot_id)
                self.confirmed.discard(slot_id)
                self.pending.add(slot_id)
                self.leds.set(slot_id, 'guide')
            elif occupied is True and previous is False and slot_id not in self.blocked:
                self.pending.discard(slot_id)
                self.confirmed.add(slot_id)
                self.leds.set(slot_id, 'ok')
        elif occupied is True and previous is False:
            self.wrong_slots.add(slot_id)
            self.leds.set(slot_id, 'wrong')
        elif occupied is False and slot_id in self.wrong_slots:
            self.wrong_slots.remove(slot_id)
            self.leds.set(slot_id, 'off')
        self._update_progress()

    def simulate_sensor(self, slot_id: int, occupied: bool) -> None:
        self.sensors.simulate(slot_id, occupied)

    async def led_self_test(self) -> None:
        await self.leds.self_test()
