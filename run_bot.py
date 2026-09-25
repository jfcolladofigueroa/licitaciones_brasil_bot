"""
LANZADOR DEL BOT — versión Python, para launchd
================================================
Hace lo mismo que run_bot.sh: verifica la integridad BD↔disco, corre el
monitor y limpia el disco, dejando log del día en logs/.

¿Por qué existe si ya había un .sh?
-----------------------------------
Por los permisos de macOS. El 14/09/2026 se cambió el plist para que llamara a
run_bot.sh en vez de a monitor.py, y la tarea dejó de ejecutarse:

    /bin/bash: .../run_bot.sh: Operation not permitted
    last exit code = 126

macOS concede los permisos de TCC **por ejecutable**, no por usuario. El python
del venv tenía acceso concedido a ~/Documents; /bin/bash no. Y como el script
vive dentro de ~/Documents, que es carpeta protegida, launchd no podía ni
leerlo.

La alternativa era dar Acceso Total al Disco a /bin/bash, lo que equivale a
concedérselo a cualquier script del sistema. Mejor mover la lógica aquí y que
el plist siga apuntando al binario que ya está autorizado.

run_bot.sh se conserva para uso manual desde la terminal, donde no hay problema
de permisos porque lo lanza tu propia sesión.

Uso:
    licitaciones_env/bin/python run_bot.py
"""

import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOGS = BASE / "logs"
LOG = LOGS / f"monitor_{date.today().isoformat()}.log"

# Cada paso: (descripción, comando, límite en segundos). Un fallo no aborta los
# siguientes: si el monitor revienta, la limpieza de disco sigue siendo útil.
#
# El límite del monitor es de 4 horas y no es exageración. El 16/09/2026 estaba
# puesto en 1 hora y lo mató en la página 49 de 344: el PNCP devuelve errores
# 500 y timeouts constantes, y el bot espera hasta 45 segundos entre reintentos.
# Un barrido completo puede pasar de las dos horas con facilidad.
#
# Cuatro horas tampoco solapa con la pasada siguiente: la de las 08:00 termina
# como muy tarde a las 12:00, y la de las 15:00 a las 19:00.
PASOS = [
    ("integridad BD ↔ disco", [sys.executable, "verificar_integridad.py"], 30 * 60),
    ("monitor", [sys.executable, "monitor.py"], 4 * 60 * 60),
    ("limpieza de disco", ["/bin/bash", str(BASE / "limpiar_disco.sh")], 30 * 60),
]


def _escribir(f, texto: str = "") -> None:
    f.write(texto + "\n")
    f.flush()


def main() -> int:
    os.chdir(BASE)
    LOGS.mkdir(exist_ok=True)
    fallos = 0

    with open(LOG, "a", encoding="utf-8") as f:
        _escribir(f)
        _escribir(f, "═" * 56)
        _escribir(f, f"  INICIO  {datetime.now():%F %T}   ({sys.executable})")
        _escribir(f, "═" * 56)

        for nombre, cmd, limite in PASOS:
            if not Path(cmd[-1]).exists():
                _escribir(f, f"⏭️  {nombre}: no existe {cmd[-1]}, se salta")
                continue
            _escribir(f, f"\n▶️  {nombre}  (límite {limite // 60} min)")
            try:
                r = subprocess.run(cmd, cwd=BASE, stdout=f, stderr=subprocess.STDOUT,
                                   timeout=limite)
                if r.returncode != 0:
                    fallos += 1
                    _escribir(f, f"⚠️  {nombre} terminó con código {r.returncode}")
            except subprocess.TimeoutExpired:
                fallos += 1
                _escribir(f, f"❌ {nombre}: superó el límite de "
                             f"{limite // 60} minutos, abortado")
            except OSError as e:
                fallos += 1
                _escribir(f, f"❌ {nombre}: {e}")

        _escribir(f, f"\n{datetime.now():%F %T}  FIN — pasos con fallo: {fallos}")

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
