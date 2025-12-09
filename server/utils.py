import os


def load_file(file_path: str, base_file_path: str = None):
    if base_file_path:
        file_path = os.path.join(os.path.dirname(base_file_path), file_path)
    with open(file_path, "r") as f:
        return f.read()
