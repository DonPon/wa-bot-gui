import logging
import shutil
import subprocess
from datetime import datetime

logger = logging.getLogger("wa-bot")

SEND_TIMEOUT_SECONDS = 180


def wacli_available() -> bool:
    return shutil.which("wacli") is not None


def build_command(jid: str, message: str) -> list[str]:
    return ["wacli", "send", "text", "--to", jid, "--message", message]


def send_message(jid: str, message: str, *, dry_run: bool = False) -> dict:
    """Envía un mensaje de WhatsApp usando wacli (vía subprocess).

    El retardo/agendamiento lo maneja el scheduler (send_at); esta función
    solo ejecuta el envío.

    Devuelve un dict con jid, message, dry_run, started_at, finished_at,
    exit_code, output y status ('success' | 'failed').
    """
    result = {
        "jid": jid,
        "message": message,
        "dry_run": dry_run,
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "exit_code": None,
        "output": "",
        "status": "failed",
    }

    cmd = build_command(jid, message)

    if dry_run:
        result["output"] = "[DRY RUN] se habría ejecutado:\n  " + " ".join(cmd)
        result["exit_code"] = 0
        result["status"] = "success"
        result["finished_at"] = datetime.now().isoformat()
        logger.info("dry-run: %s", result["output"].replace("\n", " "))
        return result

    if not wacli_available():
        result["exit_code"] = 127
        result["output"] = "wacli no está instalado o no está en el PATH"
        result["finished_at"] = datetime.now().isoformat()
        logger.error("envío: wacli no disponible")
        return result

    logger.info("enviando mensaje a %s ...", jid)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SEND_TIMEOUT_SECONDS,
        )
        result["exit_code"] = proc.returncode
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        result["output"] = out + (("\n" + err) if err else "")
        result["status"] = "success" if proc.returncode == 0 else "failed"
    except FileNotFoundError:
        result["exit_code"] = 127
        result["output"] = "wacli no está instalado o no está en el PATH"
    except subprocess.TimeoutExpired:
        result["exit_code"] = None
        result["output"] = f"timeout: wacli no respondió en {SEND_TIMEOUT_SECONDS}s"
    except Exception as e:
        result["exit_code"] = None
        result["output"] = str(e)

    result["finished_at"] = datetime.now().isoformat()
    if result["status"] == "success":
        logger.info("mensaje enviado a %s", jid)
    else:
        logger.error("fallo al enviar a %s: %s", jid, result["output"])
    return result
