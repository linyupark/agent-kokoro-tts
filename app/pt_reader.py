"""Pure-numpy reader for torch .pt (zip) files -- no torch required.

Kokoro voice files are single float tensors saved with torch.save(); this
module reconstructs them with numpy by interpreting the pickle protocol
directly (torch._utils._rebuild_tensor_v2 + storage persistent ids).
"""

from __future__ import annotations

import io
import pickle
import zipfile

import numpy as np

_STORAGE_NAME_TO_DTYPE = {
    "FloatStorage": "float32",
    "HalfStorage": "float16",
    "DoubleStorage": "float64",
    "ByteStorage": "uint8",
    "CharStorage": "int8",
    "ShortStorage": "int16",
    "IntStorage": "int32",
    "LongStorage": "int64",
    "BoolStorage": "bool",
    "BFloat16Storage": "uint16",
}

_TORCH_DTYPE_TO_NUMPY = {
    "float32": "float32",
    "float16": "float16",
    "float64": "float64",
    "uint8": "uint8",
    "int8": "int8",
    "int16": "int16",
    "int32": "int32",
    "int64": "int64",
    "bool": "bool",
}


class _StorageClass:
    """Stub for torch.FloatStorage-style classes found in the pickle."""

    def __init__(self, dtype: str) -> None:
        self.numpy_dtype = _STORAGE_NAME_TO_DTYPE.get(dtype, "float32")

    def __call__(self, *args, **kwargs):  # pragma: no cover - never constructed
        raise RuntimeError("storage classes are only used for dtype info")


class _TorchDtype:
    """Stub for torch.float32-style dtype objects."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.numpy_dtype = _TORCH_DTYPE_TO_NUMPY.get(name, "float32")


class _UtilsStub:
    """Stub for the torch._utils module; rebuild fns bound to a reader."""

    def __init__(self, reader: "_PickleReader") -> None:
        self._reader = reader
        self._rebuild_tensor_v2 = reader._rebuild_tensor_v2
        self._rebuild_tensor = reader._rebuild_tensor


class _PickleReader:
    def __init__(self, zf: zipfile.ZipFile, prefix: str) -> None:
        self._zf = zf
        self._prefix = prefix

    def find_class(self, module: str, name: str):
        if module == "torch._utils" and name in ("_rebuild_tensor_v2", "_rebuild_tensor"):
            return getattr(_UtilsStub(self), name)
        if module == "torch" and name in _STORAGE_NAME_TO_DTYPE:
            return _StorageClass(name)
        if module == "torch" and name in _TORCH_DTYPE_TO_NUMPY:
            return _TorchDtype(name)
        if module == "torch.storage" and name == "_TypedStorage":
            return _StorageClass("float32")  # dtype comes from the pid instead
        if module == "torch" and name == "storage":
            return types_module_stub()
        if module in ("collections", "builtins") or module.startswith(("numpy", "torch.nn")):
            # fall back to the real import for plain types (OrderedDict etc.)
            import importlib

            return getattr(importlib.import_module(module), name)
        raise pickle.UnpicklingError(f"unsupported pickle global {module}.{name}")

    def persistent_load(self, pid):
        if not pid or pid[0] != "storage":
            raise pickle.UnpicklingError(f"unsupported persistent id: {pid!r}")
        rest = list(pid[1:])
        # Layouts seen in the wild:
        #   legacy: (StorageClass, key, location, numel)
        #   typed:  (dtype_stub_or_str, key, location, numel)
        dtype = "float32"
        key = rest[1] if len(rest) >= 3 else rest[0]
        first = rest[0]
        if isinstance(first, _StorageClass):
            dtype = first.numpy_dtype
        elif isinstance(first, _TorchDtype):
            dtype = first.numpy_dtype
        elif isinstance(first, str) and first in _TORCH_DTYPE_TO_NUMPY:
            dtype = first
        return {"key": key, "dtype": dtype}

    def _read_storage(self, info: dict) -> np.ndarray:
        raw = self._zf.read(f"{self._prefix}data/{info['key']}")
        return np.frombuffer(raw, dtype=np.dtype(info["dtype"]))

    def _rebuild_tensor_v2(self, storage, storage_offset, size, stride, *args, **kwargs):
        return self._materialize(storage, storage_offset, size, stride)

    def _rebuild_tensor(self, storage, storage_offset, size, stride):
        return self._materialize(storage, storage_offset, size, stride)

    def _materialize(self, storage: dict, storage_offset: int, size, stride) -> np.ndarray:
        flat = self._read_storage(storage)
        size = tuple(int(s) for s in size)
        if not size:
            return flat[int(storage_offset)]
        itemsize = flat.dtype.itemsize
        window = flat[int(storage_offset):]
        strides = tuple(int(s) * itemsize for s in stride)
        return np.lib.stride_tricks.as_strided(window, shape=size, strides=strides).copy()


def types_module_stub():  # pragma: no cover - torch import in pickle
    import types as _types

    return _types.SimpleNamespace(_TypedStorage=_StorageClass("float32"))


def load_pt_tensor(path_or_bytes) -> np.ndarray:
    """Load a torch .pt file containing plain tensor(s) as numpy arrays.

    Returns a single ndarray when the file holds one tensor, otherwise a
    dict of numpy arrays / primitives mirroring the saved object.
    """
    if isinstance(path_or_bytes, (bytes, bytearray)):
        zf = zipfile.ZipFile(io.BytesIO(bytes(path_or_bytes)))
    else:
        zf = zipfile.ZipFile(path_or_bytes)

    prefix = ""
    for name in zf.namelist():
        if name.endswith("data.pkl"):
            prefix = name[: -len("data.pkl")]
            break
    if not prefix and "data.pkl" not in zf.namelist():
        raise ValueError(f"not a torch zip archive: {zf.namelist()[:5]}")

    reader = _PickleReader(zf, prefix)

    class _Unpickler(pickle.Unpickler):
        find_class = reader.find_class
        persistent_load = reader.persistent_load

    with zf.open(f"{prefix}data.pkl") as fp:
        obj = _Unpickler(fp).load()

    if isinstance(obj, dict):
        return {k: (v if not isinstance(v, np.ndarray) else v) for k, v in obj.items()}
    return obj
