from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class WorkerState:
    file_path: str
    original_file_path: str
    language_code: str
    tag_audio_events: bool
    max_subtitle_duration: float
    split_duration_min: float
    ffmpeg_available: bool
    enable_async_processing: bool
    max_concurrent_chunks: int
    max_retries: int
    api_rate_limit_per_minute: int
    api_key: str | None = None
    temp_chunks: list[str] = field(default_factory=list)
    owned_temp_chunks: list[str] = field(default_factory=list)
    combined_transcript: dict[str, Any] = field(default_factory=dict)
    current_chunk_index: int = 0
    total_chunks: int = 0
    time_offset: float = 0.0
    async_progress: dict[str, Any] = field(default_factory=dict)
    was_single_file_mode: bool = False
    extracted_audio_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkerState":
        return cls(
            file_path=str(data.get("file_path", "")),
            original_file_path=str(
                data.get("original_file_path", data.get("file_path", ""))
            ),
            language_code=str(data.get("language_code", "auto")),
            tag_audio_events=bool(data.get("tag_audio_events", False)),
            max_subtitle_duration=float(data.get("max_subtitle_duration", 7.0)),
            split_duration_min=float(data.get("split_duration_min", 90)),
            ffmpeg_available=bool(data.get("ffmpeg_available", False)),
            enable_async_processing=bool(data.get("enable_async_processing", True)),
            max_concurrent_chunks=int(data.get("max_concurrent_chunks", 3)),
            max_retries=int(data.get("max_retries", 3)),
            api_rate_limit_per_minute=int(data.get("api_rate_limit_per_minute", 30)),
            api_key=(
                str(data["api_key"])
                if data.get("api_key") is not None
                else None
            ),
            temp_chunks=[str(item) for item in data.get("temp_chunks", [])],
            owned_temp_chunks=[str(item) for item in data.get("owned_temp_chunks", [])],
            combined_transcript=(
                data.get("combined_transcript", {})
                if isinstance(data.get("combined_transcript", {}), dict)
                else {}
            ),
            current_chunk_index=int(data.get("current_chunk_index", 0)),
            total_chunks=int(data.get("total_chunks", 0)),
            time_offset=float(data.get("time_offset", 0.0)),
            async_progress=(
                data.get("async_progress", {})
                if isinstance(data.get("async_progress", {}), dict)
                else {}
            ),
            was_single_file_mode=bool(data.get("was_single_file_mode", False)),
            extracted_audio_file=(
                str(data["extracted_audio_file"])
                if data.get("extracted_audio_file") is not None
                else None
            ),
        )
