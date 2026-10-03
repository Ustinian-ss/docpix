"""子进程调用封装。

统一处理：F 盘临时目录继承、超时、输出捕获、失败时的可读报错。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import config


class EngineError(RuntimeError):
    """外部引擎执行失败。"""

    def __init__(self, message: str, *, cmd: list[str] | None = None,
                 returncode: int | None = None, stderr: str = "") -> None:
        super().__init__(message)
        self.cmd = cmd or []
        self.returncode = returncode
        self.stderr = stderr

    def as_dict(self) -> dict:
        return {
            "error": str(self),
            "command": " ".join(self.cmd) if self.cmd else None,
            "returncode": self.returncode,
            "stderr": self.stderr[-4000:] if self.stderr else "",
        }


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def base_env() -> dict[str, str]:
    """返回一个保证临时目录在 F 盘的子进程环境。"""
    config.ensure_dirs()
    env = os.environ.copy()
    env["TEMP"] = str(config.TMP_DIR)
    env["TMP"] = str(config.TMP_DIR)
    env["TMPDIR"] = str(config.TMP_DIR)
    # LibreOffice 默认把用户配置写到 %APPDATA%，这里强制重定向
    env["HOME"] = str(config.TMP_DIR)
    env["USERPROFILE"] = env.get("USERPROFILE", str(config.TMP_DIR))
    env["SAL_USE_VCLPLUGIN"] = "svp"
    return env


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 900,
    check: bool = True,
    env_extra: dict[str, str] | None = None,
    stdin_data: bytes | None = None,
) -> RunResult:
    """执行外部命令。

    ``check=True`` 时非零退出会抛 :class:`EngineError`。
    """
    env = base_env()
    if env_extra:
        env.update(env_extra)

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            input=stdin_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise EngineError(f"找不到可执行文件：{cmd[0]}", cmd=cmd) from exc
    except subprocess.TimeoutExpired as exc:
        raise EngineError(
            f"命令超时（{timeout}s）：{Path(cmd[0]).name}", cmd=cmd
        ) from exc

    stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""

    result = RunResult(proc.returncode, stdout, stderr)
    if check and not result.ok:
        detail = (stderr or stdout).strip().splitlines()
        tail = " / ".join(detail[-4:]) if detail else "无输出"
        raise EngineError(
            f"{Path(cmd[0]).name} 执行失败（退出码 {proc.returncode}）：{tail}",
            cmd=cmd,
            returncode=proc.returncode,
            stderr=stderr,
        )
    return result
