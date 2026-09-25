"""Application-owned credentials: Windows vault or process memory, never JSON."""

from __future__ import annotations

import ctypes
import hashlib
import os
import sys
from pathlib import Path

_MEMORY: dict[tuple[str, str], str | None] = {}


class CredentialError(ValueError):
    pass


def namespace(root: Path) -> str:
    # normcase matches Windows path identity; different data roots never share entries.
    identity = os.path.normcase(str(root.resolve()))
    return "ai-neko/providers/" + hashlib.sha256(identity.encode()).hexdigest()


class WindowsVault:
    """Small ctypes binding to the documented Windows Credential Manager API."""

    def __init__(self):
        from ctypes import wintypes

        class Credential(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        self.Credential = Credential
        self.dll = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self.dll.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(Credential)),
        ]
        self.dll.CredReadW.restype = wintypes.BOOL
        self.dll.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        self.dll.CredWriteW.restype = wintypes.BOOL
        self.dll.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self.dll.CredDeleteW.restype = wintypes.BOOL
        self.dll.CredFree.argtypes = [ctypes.c_void_p]
        self.dll.CredFree.restype = None

    def get(self, target: str) -> str | None:
        pointer = ctypes.POINTER(self.Credential)()
        if not self.dll.CredReadW(target, 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:  # ERROR_NOT_FOUND
                return None
            raise CredentialError("credential_read_failed")
        try:
            entry = pointer.contents
            if entry.CredentialBlobSize > 2560:
                raise CredentialError("credential_invalid")
            try:
                return ctypes.string_at(entry.CredentialBlob, entry.CredentialBlobSize).decode(
                    "utf-8"
                )
            except UnicodeError:
                raise CredentialError("credential_invalid") from None
        finally:
            self.dll.CredFree(pointer)

    def set(self, target: str, value: str | None):
        if value is None:
            if not self.dll.CredDeleteW(target, 1, 0) and ctypes.get_last_error() != 1168:
                raise CredentialError("credential_delete_failed")
            return
        encoded = value.encode("utf-8")
        if len(encoded) > 2560:
            raise CredentialError("credential_too_long")
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        entry = self.Credential(
            Type=1,
            TargetName=target,
            Comment="ai-neko provider credential",
            CredentialBlobSize=len(encoded),
            CredentialBlob=blob,
            Persist=2,
            UserName="ai-neko",
        )
        try:
            if not self.dll.CredWriteW(ctypes.byref(entry), 0):
                raise CredentialError("credential_write_failed")
        finally:
            ctypes.memset(blob, 0, len(encoded))


class Credentials:
    def __init__(self, root: Path):
        self.namespace = namespace(root)
        self.vault = WindowsVault() if sys.platform == "win32" else None
        self.storage = "windows_credential_manager" if self.vault else "process_memory"

    def get(self, kind: str) -> str | None:
        self._kind(kind)
        key = (self.namespace, kind)
        if key in _MEMORY:
            return _MEMORY[key]
        if self.vault:
            stored = self.vault.get(self.namespace + "/" + kind)
            if stored is not None:
                return stored
        return os.environ.get(f"AI_NEKO_{kind.upper()}_API_KEY") or None

    def set(self, kind: str, value: str | None):
        self._kind(kind)
        if self.vault:
            self.vault.set(self.namespace + "/" + kind, value)
        _MEMORY[(self.namespace, kind)] = value

    @staticmethod
    def _kind(kind):
        if kind not in {"model", "search"}:
            raise CredentialError("credential_kind_invalid")
