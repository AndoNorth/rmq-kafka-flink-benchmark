import os


def get_env(key: str, default: str) -> str:
    # Like Go's getEnv: an empty value counts as unset.
    return os.environ.get(key) or default
