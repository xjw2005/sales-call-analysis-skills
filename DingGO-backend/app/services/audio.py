from pathlib import Path


def duration_ms(path: Path) -> int:
    """读取音频时长；格式无法识别时返回 0（由客户端上报的时长兜底）"""
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(path)
        if audio is not None and audio.info and audio.info.length:
            return int(audio.info.length * 1000)
    except Exception:  # noqa: BLE001 - 任何解析异常都按未知时长处理
        pass
    return 0
