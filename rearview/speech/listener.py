from __future__ import annotations

import asyncio
import queue
import threading
import numpy as np
import sounddevice as sd
from concurrent.futures import ThreadPoolExecutor
from faster_whisper import WhisperModel

from rearview.config import get_config

_SAMPLE_RATE = 16000
_CHANNELS = 1
_DTYPE = "float32"
_BLOCKSIZE = 4096
_CHUNK_SECONDS = 2


class SpeechListener:
    def __init__(self, loop: asyncio.AbstractEventLoop, out_queue: asyncio.Queue):
        # out_queue receives strings (transcript chunks) as they come in
        self.loop = loop
        self.out_queue = out_queue
        self._model: WhisperModel = None
        self._running = False
        self._thread: threading.Thread = None
        self._audio_queue: queue.Queue = queue.Queue()  # raw audio chunks from sounddevice
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._stream: sd.InputStream = None

    def start(self) -> None:
        """Load model (lazy), start sounddevice InputStream, start processing thread."""
        if self._running:
            return

        if self._model is None:
            model_size = get_config().teleprompter.stt_model
            self._model = WhisperModel(model_size, device="cpu", compute_type="int8")

        self._running = True

        self._stream = sd.InputStream(
            samplerate=_SAMPLE_RATE,
            channels=_CHANNELS,
            dtype=_DTYPE,
            blocksize=_BLOCKSIZE,
            callback=self._audio_callback,
        )
        self._stream.start()

        self._thread = threading.Thread(target=self._processing_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop stream, signal thread to exit, wait for join."""
        if not self._running:
            return

        self._running = False

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        # Unblock the processing thread if it is waiting on the queue
        self._audio_queue.put(None)

        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def _audio_callback(self, indata: np.ndarray, frames: int, time, status) -> None:
        """sounddevice callback — put chunk into _audio_queue."""
        if status:
            pass  # status messages (overflow, etc.) are non-fatal
        self._audio_queue.put(indata.copy())

    def _processing_loop(self) -> None:
        """Accumulate audio from _audio_queue into buffer.

        Every 2 seconds of audio, run _transcribe(buffer) in executor.
        Put result string into out_queue via loop.call_soon_threadsafe.
        """
        chunk_size = _SAMPLE_RATE * _CHUNK_SECONDS  # samples per 2-second window
        buffer = np.empty(0, dtype=np.float32)

        while self._running:
            try:
                chunk = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if chunk is None:
                # Sentinel value — stop signal received
                break

            # Flatten to 1-D (blocksize, 1) -> (blocksize,)
            buffer = np.concatenate([buffer, chunk.flatten()])

            if len(buffer) >= chunk_size:
                segment = buffer[:chunk_size]
                buffer = buffer[chunk_size:]

                future = self._executor.submit(self._transcribe, segment)

                def _on_done(f: "concurrent.futures.Future") -> None:
                    text = f.result()
                    if text:
                        self.loop.call_soon_threadsafe(
                            self.out_queue.put_nowait, text
                        )

                future.add_done_callback(_on_done)

        # Drain any remaining audio when stopping
        if len(buffer) > 0:
            future = self._executor.submit(self._transcribe, buffer)
            text = future.result()
            if text:
                self.loop.call_soon_threadsafe(self.out_queue.put_nowait, text)

    def _transcribe(self, audio: np.ndarray) -> str:
        """Run faster-whisper on audio chunk.

        Returns concatenated text of all segments.
        Uses beam_size=5, language="en", vad_filter=True.
        """
        segments, _info = self._model.transcribe(
            audio,
            beam_size=5,
            language="en",
            vad_filter=True,
        )
        return " ".join(segment.text.strip() for segment in segments if segment.text.strip())
