"""Bankr CLI backend — wraps `bankr launch ...` (verified: docs.bankr.bot/cli).

Runs the CLI non-interactively (`--ni`) so it never hangs in a container. When
`simulate=True` we pass `--simulate` (build tx without broadcasting). The CLI
reads auth from ~/.bankr/config.json or BANKR_API_KEY / BANKR_PRIVATE_KEY in the
environment — we do NOT pass secrets on the command line.
"""

from __future__ import annotations

import asyncio
import json
import re

from ..logging import get_logger
from ..models import LaunchRequest, LaunchResult, LaunchStatus
from .base import build_cli_args

log = get_logger("launcher.cli")

_ADDR_RE = re.compile(r"0x[a-fA-F0-9]{40}")


class BankrCliBackend:
    def __init__(self, binary: str = "bankr", simulate: bool = True, timeout: float = 180.0):
        self.binary = binary
        self.simulate = simulate
        self.timeout = timeout

    async def launch(self, req: LaunchRequest) -> LaunchResult:
        args = [self.binary, "--ni", *build_cli_args(req, simulate=self.simulate)]
        # Ask the CLI for machine-readable output when supported; harmless if not.
        printable = " ".join(a if " " not in a else f'"{a}"' for a in args)
        log.info("CLI launch: %s", printable)
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except FileNotFoundError:
            return LaunchResult(
                request_id=req.id,
                status=LaunchStatus.FAILED,
                error=f"bankr CLI not found (binary={self.binary!r}). `npm i -g @bankr/cli`",
            )
        except TimeoutError:
            return LaunchResult(
                request_id=req.id,
                status=LaunchStatus.FAILED,
                error=f"bankr CLI timed out after {self.timeout}s",
            )

        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")

        if proc.returncode != 0:
            return LaunchResult(
                request_id=req.id,
                status=LaunchStatus.FAILED,
                error=(stderr or stdout or f"exit code {proc.returncode}")[:800],
                raw={"stdout": stdout, "stderr": stderr, "returncode": proc.returncode},
            )

        return self._parse(req, stdout, stderr)

    def _parse(self, req: LaunchRequest, stdout: str, stderr: str) -> LaunchResult:
        parsed = self._try_json(stdout)
        token_address = ""
        pool_id = ""
        pool_url = ""
        if parsed:
            token_address = (
                parsed.get("tokenAddress") or parsed.get("address") or parsed.get("token") or ""
            )
            pool_id = parsed.get("poolId", "")
            pool_url = parsed.get("poolUrl") or parsed.get("url", "")
        if not token_address:
            m = _ADDR_RE.search(stdout)
            token_address = m.group(0) if m else ""

        status = (
            LaunchStatus.SIMULATED
            if self.simulate
            else (LaunchStatus.CONFIRMED if token_address else LaunchStatus.SUBMITTED)
        )
        return LaunchResult(
            request_id=req.id,
            status=status,
            token_address=token_address,
            pool_id=pool_id,
            pool_url=pool_url,
            raw={"stdout": stdout[:2000], "stderr": stderr[:1000], "parsed": parsed or {}},
        )

    @staticmethod
    def _try_json(text: str) -> dict | None:
        text = text.strip()
        # Whole-blob JSON first, then last {...} block.
        for candidate in (text, text[text.rfind("{") : text.rfind("}") + 1] if "{" in text else ""):
            if not candidate:
                continue
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
        return None
