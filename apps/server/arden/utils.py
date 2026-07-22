import time


def truncate(value: str, max_len: int) -> str:
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


def ms_now() -> int:
    return time.monotonic_ns() // 1_000_000
