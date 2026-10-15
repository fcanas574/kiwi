"""Authenticated local REST API compatible with Kiwi plus three-crop batches."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import asyncio
import io
import json
import os
import secrets

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from engine import Engine


def create_app(factory=Engine, key=None):
    key = key if key is not None else os.environ.get('KIWI_API_KEY', '')
    if len(key) < 16:
        raise RuntimeError('Configura KIWI_API_KEY con al menos 16 caracteres.')
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='kiwi-model')
    gate = asyncio.Lock()
    engine = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal engine
        loop = asyncio.get_running_loop()
        engine = await loop.run_in_executor(executor, factory)
        blank = Image.new('RGB', (448, 448), 'white')
        # Prime MPS/MLX kernel shapes before advertising readiness.
        for size in ((960, 720), (720, 960), (960, 540), (540, 960), (960, 480)):
            await loop.run_in_executor(executor, engine.locate, Image.new('RGB', size, 'white'))
        for count in (1, 2, 3):
            await loop.run_in_executor(executor, engine.classify_many, [blank] * count)
        # Prime real-image preprocessing and all common crop batch shapes.
        # Optional reference files belong to this project, not camera uploads.
        from pathlib import Path
        from engine import REPO
        reference = REPO / 'mac/results/three-reference-montage.jpg'
        if reference.exists() and isinstance(engine, Engine):
            with Image.open(reference) as image:
                image = image.convert('RGB')
                located = await loop.run_in_executor(executor, engine.locate, image)
                crops = [image.crop(item['box']) for item in located['objects'][:6]]
                for offset in range(0, len(crops), 3):
                    await loop.run_in_executor(executor, engine.classify_many, crops[offset:offset + 3])
        try:
            yield
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title='Kiwi · reconocimiento local', lifespan=lifespan)

    def authorize(authorization: str | None = Header(default=None)):
        if not secrets.compare_digest(authorization or '', 'Bearer ' + key):
            raise HTTPException(401, 'Clave incorrecta.')

    async def read_image(file):
        raw = await file.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise HTTPException(413, 'La imagen supera 2 MB.')
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 4_000_000:
                    raise HTTPException(413, 'La imagen supera 4 megapíxeles.')
                return ImageOps.exif_transpose(image).convert('RGB')
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise HTTPException(422, 'Imagen inválida.')

    def parse(value):
        try:
            data = json.loads(value)
        except (ValueError, TypeError):
            raise HTTPException(422, 'Correlación inválida.')
        if not isinstance(data, dict):
            raise HTTPException(422, 'Correlación inválida.')
        return data

    async def invoke(method, value):
        if gate.locked():
            raise HTTPException(503, 'Modelo ocupado; envía el siguiente cuadro sin acumular solicitudes.')
        async with gate:
            future = asyncio.get_running_loop().run_in_executor(executor, method, value)
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                # Keep the gate until native model work ends, even if the client leaves.
                await future
                raise

    @app.get('/health', dependencies=[Depends(authorize)])
    async def health():
        return {'ok': engine is not None, 'mode': 'mlx_dino_qwen', 'calibrated': False,
                'capabilities': {'predict': True, 'detect': True, 'classify': True,
                                 'classify_batch': True}, 'batch_limit': 3}

    @app.post('/classify', dependencies=[Depends(authorize)])
    async def classify(file: UploadFile = File(...), correlation: str = Form('{}')):
        metadata = parse(correlation)
        image = await read_image(file)
        result = await invoke(engine.classify, image)
        return {'correlation': metadata, 'result': result}

    @app.post('/detect', dependencies=[Depends(authorize)])
    async def detect(file: UploadFile = File(...), correlation: str = Form('{}')):
        metadata = parse(correlation)
        result = await invoke(engine.locate, await read_image(file))
        return {'correlation': metadata, 'result': result}

    @app.post('/classify-batch', dependencies=[Depends(authorize)])
    async def classify_batch(files: list[UploadFile] = File(...), correlations: str = Form(...)):
        try:
            metadata = json.loads(correlations)
        except (ValueError, TypeError):
            raise HTTPException(422, 'Correlaciones inválidas.')
        if not 1 <= len(files) <= 3 or not isinstance(metadata, list) or len(metadata) != len(files) or not all(isinstance(item, dict) for item in metadata):
            raise HTTPException(422, 'Envía de uno a tres recortes con una correlación por recorte.')
        images = [await read_image(file) for file in files]
        results = await invoke(engine.classify_many, images)
        if len(results) != len(images):
            raise HTTPException(500, 'Cantidad de resultados inconsistente.')
        return {'items': [{'correlation': info, 'result': result} for info, result in zip(metadata, results)]}

    @app.post('/predict', dependencies=[Depends(authorize)])
    async def predict(file: UploadFile = File(...)):
        return await invoke(engine.identify, await read_image(file))

    return app


if __name__ == '__main__':
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--host')
    parser.add_argument('--port', type=int, default=8003)
    parser.add_argument('--cert')
    parser.add_argument('--key')
    parser.add_argument('--config', default=str(__import__('pathlib').Path(__file__).with_name('.env')))
    args = parser.parse_args()
    from pathlib import Path
    if Path(args.config).exists():
        configuration = json.loads(Path(args.config).read_text())
        os.environ['KIWI_API_KEY'] = configuration['api_key']
        args.host = args.host or configuration.get('host')
        args.cert = args.cert or configuration.get('certfile')
        args.key = args.key or configuration.get('keyfile')
    args.host = args.host or '127.0.0.1'
    if args.host != '127.0.0.1' and not (args.cert and args.key):
        raise SystemExit('Para servir a la Raspberry configura certificado y clave HTTPS.')
    uvicorn.run(create_app(), host=args.host, port=args.port,
                ssl_certfile=args.cert, ssl_keyfile=args.key)
