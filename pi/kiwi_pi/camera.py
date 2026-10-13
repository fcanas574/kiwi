"""Cámara de la Raspberry Pi (Camera Module con picamera2 o webcam USB con OpenCV)."""
from __future__ import annotations

import io
import threading
import time

from PIL import Image, ImageOps

from .config import Config

STREAM_SIZE = (640, 480)


def to_jpeg(image: Image.Image, max_side: int = 1280, quality: int = 88) -> bytes:
    image = image.convert('RGB')
    image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=quality)
    return buffer.getvalue()


def normalize_upload(data: bytes) -> bytes:
    """Corrige la orientación EXIF, reduce el tamaño y vuelve a codificar en JPEG."""
    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image)
        return to_jpeg(image)


class Camera:
    name = 'none'
    available = False

    def __init__(self, cfg: Config):
        self.section = cfg.section('camera')
        self.rotation = int(self.section.get('rotation', 0))
        self._frame: bytes | None = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.available:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name='camera')
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self._close()

    def _loop(self) -> None:
        delay = 1 / max(1, int(self.section.get('stream_fps', 10)))
        while self._running:
            try:
                image = self._rotate(self._grab())
                image.thumbnail(STREAM_SIZE)
                frame = to_jpeg(image, max_side=STREAM_SIZE[0], quality=70)
                with self._lock:
                    self._frame = frame
            except Exception as exc:  # una desconexión no debe tumbar el servidor
                print('Cámara:', exc)
                time.sleep(1)
            time.sleep(delay)

    def _rotate(self, image: Image.Image) -> Image.Image:
        return image.rotate(-self.rotation, expand=True) if self.rotation else image

    def latest_frame(self) -> bytes | None:
        with self._lock:
            return self._frame

    def capture(self) -> bytes:
        return to_jpeg(self._rotate(self._grab_full()))

    def _grab(self) -> Image.Image:
        raise NotImplementedError

    def _grab_full(self) -> Image.Image:
        return self._grab()

    def _close(self) -> None:
        pass


class Picamera2Camera(Camera):
    name = 'picamera2'

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        from picamera2 import Picamera2
        self.cam = Picamera2()
        size = (int(self.section.get('width', 1280)), int(self.section.get('height', 960)))
        self.cam.configure(self.cam.create_video_configuration(main={'size': size, 'format': 'RGB888'}))
        self.cam.start()
        self.available = True

    def _grab(self) -> Image.Image:
        # RGB888 en picamera2 entrega el orden BGR en memoria.
        return Image.fromarray(self.cam.capture_array('main')[:, :, ::-1])

    def _close(self) -> None:
        self.cam.stop()


class OpenCVCamera(Camera):
    name = 'opencv'

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        import cv2
        self.cv2 = cv2
        self.cap = cv2.VideoCapture(int(self.section.get('opencv_index', 0)))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(self.section.get('width', 1280)))
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.section.get('height', 960)))
        ok, _ = self.cap.read()
        if not ok:
            self.cap.release()
            raise RuntimeError('La webcam no entrega imágenes.')
        self.available = True
        self._cap_lock = threading.Lock()

    def _grab(self) -> Image.Image:
        with self._cap_lock:
            ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError('No se pudo leer la webcam.')
        return Image.fromarray(self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB))

    def _close(self) -> None:
        self.cap.release()


def make_camera(cfg: Config) -> Camera:
    driver = cfg.section('camera').get('driver', 'auto')
    order = {'auto': [Picamera2Camera, OpenCVCamera], 'picamera2': [Picamera2Camera],
             'opencv': [OpenCVCamera], 'none': []}[driver]
    for cls in order:
        try:
            camera = cls(cfg)
            print('Cámara:', camera.name)
            return camera
        except Exception as exc:
            print(f'Cámara {cls.name} no disponible: {exc}')
    print('Sin cámara local: la página usará la cámara del navegador o subir foto.')
    return Camera(cfg)
