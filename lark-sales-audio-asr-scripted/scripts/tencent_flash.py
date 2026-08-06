#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯云录音文件识别极速版：签名、同步请求与统一结果转换。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path
from typing import Any


HOST = "asr.cloud.tencent.com"
SUPPORTED_FORMATS = {
    "wav", "pcm", "ogg-opus", "speex", "silk", "mp3", "m4a", "aac", "amr"
}


class RecognitionUncertain(RuntimeError):
    """请求已发送，但客户端未能确认服务端是否已完成计费识别。"""


def environment_value(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                stored, _ = winreg.QueryValueEx(key, name)
            return str(stored)
        except (ImportError, FileNotFoundError, OSError):
            pass
    return ""


def credentials(config: dict[str, Any]) -> tuple[str, str, str]:
    app_id = str(environment_value("TENCENT_ASR_APP_ID") or config.get("app_id") or "").strip()
    secret_id = str(environment_value("TENCENT_ASR_SECRET_ID") or "").strip()
    secret_key = str(environment_value("TENCENT_ASR_SECRET_KEY") or "").strip()
    missing = [
        name
        for name, value in (
            ("TENCENT_ASR_APP_ID 或 tencent_flash.app_id", app_id),
            ("TENCENT_ASR_SECRET_ID", secret_id),
            ("TENCENT_ASR_SECRET_KEY", secret_key),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("缺少腾讯 ASR 凭证: " + "、".join(missing))
    return app_id, secret_id, secret_key


def voice_format(path: Path) -> str:
    value = path.suffix.lower().lstrip(".")
    if value == "ogg":
        value = "ogg-opus"
    if value not in SUPPORTED_FORMATS:
        raise ValueError(f"腾讯极速版不支持音频格式: {path.suffix or '<无扩展名>'}")
    return value


def ffmpeg_executable(config: dict[str, Any]) -> Path:
    configured = str(config.get("ffmpeg_path") or "").strip()
    if configured:
        path = Path(configured)
        if path.is_file():
            return path
        raise RuntimeError(f"配置的 ffmpeg 不存在: {path}")
    dependency_root = Path(__file__).resolve().parent.parent / ".deps" / "imageio_ffmpeg" / "binaries"
    matches = sorted(dependency_root.glob("ffmpeg-*.exe"))
    if not matches:
        raise RuntimeError("找不到腾讯音频标准化所需的 ffmpeg")
    return matches[0]


def is_standard_wav(path: Path) -> bool:
    if path.suffix.lower() != ".wav":
        return False
    try:
        with wave.open(str(path), "rb") as audio:
            return (
                audio.getnchannels() == 1
                and audio.getframerate() == 16000
                and audio.getsampwidth() == 2
                and audio.getnframes() > 0
            )
    except (OSError, wave.Error):
        return False


def standardize_audio(path: Path, config: dict[str, Any]) -> Path:
    if not config.get("normalize_audio", True) or is_standard_wav(path):
        return path
    target = path.parent / "audio.tencent.wav"
    if target.exists() and target.stat().st_mtime >= path.stat().st_mtime and is_standard_wav(target):
        return target
    proc = subprocess.run(
        [
            str(ffmpeg_executable(config)),
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-i", str(path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(target),
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode or not is_standard_wav(target):
        raise RuntimeError(f"腾讯音频标准化失败: {proc.stderr[-1000:]}")
    if target.stat().st_size > 100 * 1024 * 1024:
        raise RuntimeError("标准化后的 WAV 超过腾讯极速版 100MB 限制")
    return target


def request_params(
    config: dict[str, Any],
    secret_id: str,
    audio_format: str,
    timestamp: int,
) -> dict[str, str]:
    params = {
        "secretid": secret_id,
        "engine_type": str(config.get("engine_type") or "16k_zh_en"),
        "voice_format": audio_format,
        "timestamp": str(timestamp),
        "speaker_diarization": str(int(config.get("speaker_diarization", 1))),
        "filter_dirty": str(int(config.get("filter_dirty", 0))),
        "filter_modal": str(int(config.get("filter_modal", 0))),
        "filter_punc": str(int(config.get("filter_punc", 0))),
        "convert_num_mode": str(int(config.get("convert_num_mode", 1))),
        "word_info": str(int(config.get("word_info", 0))),
        "first_channel_only": str(int(config.get("first_channel_only", 1))),
    }
    hotwords = config.get("hotwords") or []
    if hotwords:
        weight = int(config.get("hotword_weight", 10))
        if weight not in {*range(1, 12), 100}:
            raise ValueError("hotword_weight 必须为 1-11 或 100")
        if len(hotwords) > 128:
            raise ValueError("腾讯临时热词不能超过 128 个")
        params["hotword_list"] = ",".join(f"{word}|{weight}" for word in hotwords)
    return params


def encoded_query(params: dict[str, str]) -> str:
    return urllib.parse.urlencode(
        sorted(params.items()),
        quote_via=urllib.parse.quote,
        safe="",
    )


def canonical_query(params: dict[str, str]) -> str:
    return "&".join(f"{key}={value}" for key, value in sorted(params.items()))


def signature(app_id: str, query: str, secret_key: str) -> str:
    source = f"POST{HOST}/asr/flash/v1/{app_id}?{query}".encode("utf-8")
    digest = hmac.new(secret_key.encode("utf-8"), source, hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def recognize_file(
    path: Path,
    config: dict[str, Any],
    *,
    timestamp: int,
) -> dict[str, Any]:
    app_id, secret_id, secret_key = credentials(config)
    params = request_params(config, secret_id, voice_format(path), timestamp)
    signing_query = canonical_query(params)
    transport_query = encoded_query(params)
    url = f"https://{HOST}/asr/flash/v1/{app_id}?{transport_query}"
    body = path.read_bytes()
    if not body:
        raise ValueError("音频文件为空")
    if len(body) > 100 * 1024 * 1024:
        raise ValueError("腾讯极速版请求体不能超过 100MB")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Host": HOST,
            "Authorization": signature(app_id, signing_query, secret_key),
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(body)),
        },
    )
    timeout = float(config.get("timeout_seconds", 300))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1000]
        raise RuntimeError(f"腾讯 ASR HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise RecognitionUncertain(
            "腾讯 ASR 请求已发送但响应状态未知；为避免重复计费，禁止自动重试"
        ) from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"腾讯 ASR 返回非 JSON: {raw[:500]}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("腾讯 ASR 返回值顶层不是 JSON 对象")
    if str(value.get("code")) != "0":
        raise RuntimeError(
            f"腾讯 ASR 失败 code={value.get('code')}: {value.get('message') or ''}"
        )
    return value


def normalize_response(value: dict[str, Any]) -> dict[str, Any]:
    channels = value.get("flash_result")
    if not isinstance(channels, list) or not channels:
        raise ValueError("腾讯 ASR 结果缺少 flash_result")
    utterances: list[dict[str, Any]] = []
    full_text: list[str] = []
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        channel_id = channel.get("channel_id", 0)
        if channel.get("text"):
            full_text.append(str(channel["text"]))
        sentences = channel.get("sentence_list")
        if not isinstance(sentences, list):
            continue
        for sentence in sentences:
            if not isinstance(sentence, dict) or not str(sentence.get("text") or "").strip():
                continue
            speaker = sentence.get("speaker_id", channel_id)
            utterances.append({
                "text": str(sentence["text"]).strip(),
                "start_time": int(sentence.get("start_time") or 0),
                "end_time": int(sentence.get("end_time") or 0),
                "speaker_id": speaker,
                "additions": {
                    "speaker": str(speaker),
                    "channel_id": channel_id,
                    "emotional_energy": sentence.get("emotional_energy"),
                    "speech_speed": sentence.get("speech_speed"),
                },
                "word_list": sentence.get("word_list") or [],
            })
    utterances.sort(key=lambda item: (item["start_time"], item["end_time"]))
    text = "".join(full_text).strip() or "".join(item["text"] for item in utterances)
    if not text or not utterances:
        raise ValueError("腾讯 ASR 结果没有可用文本或句子时间戳")
    return {
        "data": {
            "result": {
                "business_code": "0",
                "text": text,
                "utterances": utterances,
                "provider": "tencent_flash",
                "request_id": value.get("request_id"),
                "audio_duration": value.get("audio_duration"),
            }
        }
    }
