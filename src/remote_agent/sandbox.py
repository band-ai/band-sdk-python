"""Linux execution policy for generated Python. No access to host state or providers."""

from __future__ import annotations

import ctypes
import errno
import math
import shutil
import sys
from pathlib import Path

if sys.platform == "linux":
    import resource
from typing import TYPE_CHECKING

from remote_agent.types import JsonObject

if TYPE_CHECKING:
    from remote_agent.program import Artifact


PROFILE = "remote-agent/linux-pure-python/1"
MEMORY_BYTES = 256 * 1024 * 1024


class SandboxError(Exception):
    pass


def policy(timeout: float) -> JsonObject:
    return {
        "profile": PROFILE,
        "memory_bytes": MEMORY_BYTES,
        "cpu_seconds": max(1, math.ceil(timeout)),
    }


def command(artifact: Artifact, *, timeout: float) -> tuple[list[str], JsonObject]:
    """Construct a closed filesystem and isolated process/network namespaces."""
    executable = shutil.which("bwrap")
    if sys.platform != "linux" or executable is None:
        raise SandboxError(
            "Linux bubblewrap is required; refusing an unsandboxed callback"
        )
    # A venv may copy the launcher instead of symlinking it. Its base interpreter
    # remains available in the read-only /usr mount; the venv itself stays hidden.
    python = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    if not str(python).startswith("/usr/"):
        raise SandboxError("The sandbox requires a Python runtime under /usr")
    package = Path(__file__).resolve().parent
    argv = [
        executable,
        "--unshare-all",
        "--unshare-user",
        "--die-with-parent",
        "--new-session",
        "--disable-userns",
        "--assert-userns-disabled",
        "--clearenv",
        "--cap-drop",
        "ALL",
    ]
    for directory in ("/usr", "/lib", "/lib64"):
        if Path(directory).exists():
            argv.extend(["--ro-bind", directory, directory])
    argv.extend(
        [
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--dir",
            "/tmp",
            "--dir",
            "/app",
            "--ro-bind",
            str(package),
            "/app/remote_agent",
            "--dir",
            "/program",
            "--ro-bind",
            artifact.path,
            "/program/program.py",
            "--chdir",
            "/program",
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--setenv",
            "TZ",
            "UTC",
            "--remount-ro",
            "/",
            str(python),
            "-I",
            "-B",
            "-c",
            "import sys;sys.path.insert(0,'/app');from remote_agent.worker import main;main()",
        ]
    )
    return argv, policy(timeout)


def restrict(policy: object) -> None:
    """Apply hard resource limits and a syscall filter before evaluating source."""
    if sys.platform != "linux":
        raise SandboxError("Linux process limits and syscall isolation are required")
    if (
        not isinstance(policy, dict)
        or set(policy) != {"profile", "memory_bytes", "cpu_seconds"}
        or policy["profile"] != PROFILE
        or policy["memory_bytes"] != MEMORY_BYTES
        or type(policy["cpu_seconds"]) is not int
        or not 1 <= policy["cpu_seconds"] <= 3600
    ):
        raise SandboxError("Invalid isolation policy")
    seccomp = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    seccomp.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    seccomp.seccomp_rule_add.restype = ctypes.c_int
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_load.restype = ctypes.c_int
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.restype = None
    context = seccomp.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise SandboxError("Unable to initialize syscall filter")
    try:
        denied = (
            "clone",
            "clone3",
            "fork",
            "vfork",
            "execve",
            "execveat",
            "socket",
            "socketpair",
            "ptrace",
            "process_vm_readv",
            "process_vm_writev",
            "mount",
            "umount2",
            "pivot_root",
            "unshare",
            "setns",
            "keyctl",
            "add_key",
            "request_key",
            "bpf",
            "perf_event_open",
            "kexec_load",
            "kexec_file_load",
            "init_module",
            "finit_module",
            "delete_module",
            "open_by_handle_at",
            "name_to_handle_at",
            "io_uring_setup",
            "io_uring_enter",
            "io_uring_register",
            "userfaultfd",
            "reboot",
            "swapon",
            "swapoff",
        )
        for name in denied:
            number = seccomp.seccomp_syscall_resolve_name(name.encode("ascii"))
            if number == -1:  # __NR_SCMP_ERROR: libseccomp does not know the name.
                raise SandboxError("Unable to resolve required syscall: " + name)
            if number <= -10000:
                continue  # Known single syscall absent from this architecture.
            # Multiplexed pseudo syscalls (e.g. socket=-101) still need rules.
            # Other invalid/reserved numbers fail closed through rule_add.
            if (
                seccomp.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0)
                != 0
            ):
                raise SandboxError("Unable to install syscall rule: " + name)
        limits = [
            (resource.RLIMIT_AS, MEMORY_BYTES),
            (resource.RLIMIT_CPU, policy["cpu_seconds"]),
            (resource.RLIMIT_NPROC, 0),
            (resource.RLIMIT_NOFILE, 32),
            (resource.RLIMIT_FSIZE, 0),
            (resource.RLIMIT_CORE, 0),
        ]
        for kind, value in limits:
            resource.setrlimit(kind, (value, value))
        if seccomp.seccomp_load(context) != 0:
            raise SandboxError("Unable to activate syscall filter")
    finally:
        seccomp.seccomp_release(context)
