from __future__ import annotations

from typing import Callable

from .async_chunk_processor import AsyncChunkProcessor


class AsyncChunkCoordinator:
    def __init__(
        self,
        *,
        max_concurrent_chunks: int,
        max_retries: int,
        api_key_manager,
        api_rate_limit_per_minute: int,
        logger: Callable[[str], None],
    ):
        self._logger = logger
        self.processor = AsyncChunkProcessor(
            max_concurrent_chunks=max_concurrent_chunks,
            max_retries=max_retries,
            api_key_manager=api_key_manager,
        )
        self.processor.max_requests_per_minute = api_rate_limit_per_minute

    def connect(
        self,
        *,
        chunk_started,
        chunk_completed,
        chunk_failed,
        all_completed,
        processing_failed,
        progress_updated,
    ):
        self.processor.chunk_started.connect(chunk_started)
        self.processor.chunk_completed.connect(chunk_completed)
        self.processor.chunk_failed.connect(chunk_failed)
        self.processor.all_chunks_completed.connect(all_completed)
        self.processor.processing_failed.connect(processing_failed)
        self.processor.progress_updated.connect(progress_updated)

    def start(
        self,
        *,
        chunk_paths: list[str],
        split_duration_sec: int,
        language_code: str,
        tag_audio_events: bool,
        ffmpeg_available: bool,
    ) -> bool:
        return self.processor.process_chunks_async(
            chunk_paths=chunk_paths,
            split_duration_sec=split_duration_sec,
            language_code=language_code,
            tag_audio_events=tag_audio_events,
            ffmpeg_available=ffmpeg_available,
            log_callback=self._logger,
        )
