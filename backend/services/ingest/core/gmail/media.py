"""Audio and video attachments (metadata stub until STT)."""


def extract_media_stub(*, mime: str, filename: str, size: int) -> str:
    # shortcut: no STT yet; stub keeps media searchable in full-text + drive_url
    mib = size / (1024 * 1024)
    return (
        f"Media attachment: {filename}\n"
        f"Type: {mime or 'unknown'}\n"
        f"Size: {mib:.2f} MiB\n"
        f"Note: no transcript; content is stored on Google Drive."
    )