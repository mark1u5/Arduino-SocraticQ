# Handoff de contexto — Dev 1 / SocraticQ

> Documento para entregar a otro asistente de IA o desarrollador que continúe
> el trabajo. No contiene secretos, contraseñas ni claves de API.

## 1. Proyecto actual

El repositorio implementa **SocraticQ + RawTree Agentic Cortex** para el UPC
Datathon 2026. La meta es un tutor de matemáticas socrático que funciona de
forma local en un **Arduino UNO Q**: no debe depender de Internet para generar
pistas durante la demostración.

El sistema completo tiene tres partes conectadas:

```text
Controles físicos → MCU Arduino → bridge local → motor socrático local
                                              ↓
                                      telemetría de aprendizaje
                                              ↓
                            RawTree (análisis/agente) → dashboard Tukey
```

- **Track principal:** tutor de matemáticas offline en Arduino UNO Q / Qualcomm
  QRB2210.
- **RawTree:** analiza los eventos de los estudiantes, detecta bloqueos y crea
  intervenciones pedagógicas.
- **Tukey:** muestra distribuciones, boxplots, calibración de confianza y
  efectos de las intervenciones.

El objetivo de la demo es que una interacción física produzca una pista
socrática local, se registre como telemetría y, cuando haya red, alimente los
componentes RawTree y Tukey.

## 2. Estado del repositorio y Git

- Repositorio local:
  `C:\Users\bbaro\OneDrive\Documentos\Qualcom\Datathon-FME-2026`
- Remoto:
  `https://github.com/mark1u5/Datathon-FME-2026.git`
- Rama actual: `dev1/hardware`.
- No se ha realizado ningún `git push` en esta sesión.
- Hay cambios locales sin confirmar únicamente dentro de `hardware/`.

Antes de trabajar, comprobar:

```powershell
cd 'C:\Users\bbaro\OneDrive\Documentos\Qualcom\Datathon-FME-2026'
git status
git switch dev1/hardware
git fetch origin
git merge origin/main
```

No hacer `push` a `main`. El trabajo de Dev 1 se sube a `dev1/hardware` y se
integra mediante Pull Request cuando el equipo lo decida.

## 3. Reglas obligatorias del proyecto

La guía de trabajo está en el `AGENTS.md` de la raíz. Antes de planificar o
editar, leer:

1. `SYNC_BOARD.md`
2. `TEAM_EXECUTION_PLAN.md`
3. `contracts/schemas.py`

Dev 1 solo puede crear o editar dentro de `hardware/`. No modificar:

- `contracts/`
- `TEAM_EXECUTION_PLAN.md`
- `.env.example`
- carpetas propiedad de Dev 2, Dev 3 o Dev 4

Los contratos JSON están congelados. Nunca añadir, borrar o renombrar claves
sin una coordinación explícita del equipo y un cambio realizado desde `main`.

Antes de cada commit:

```powershell
python -m contracts.check_contracts
python -m unittest hardware.tests.test_bridge
git status
```

El tablero actual marca a Dev 2 y Dev 3 como no disponibles todavía. Por eso
Dev 1 debe usar mocks y fixtures, sin bloquearse esperando integraciones.

## 4. Responsabilidad exacta de Dev 1

Dev 1 implementa la capa hardware y su puente con el software:

1. Firmware del MCU: lee diales y botones; controla LED RGB, buzzer y LCD si
   existe.
2. Simulador de MCU para desarrollar sin depender de la placa.
3. Puente Python entre eventos del hardware, motor socrático de Dev 2 y
   endpoint de telemetría de Dev 3.
4. Cola SQLite local para no perder telemetría cuando no haya Wi-Fi.
5. Integración con la intervención de RawTree: debe poder activar el LED
   morado `BOTTLENECK_ALERT`.

## 5. Contratos que no se pueden romper

### Eventos del MCU

Los eventos contienen obligatoriamente:

```json
{
  "schema_v": 1,
  "device_id": "unoq-01",
  "seq": 42,
  "ts_ms": 184290,
  "event_type": "HINT_REQUEST",
  "problem_id": "CAM-2023-N4-17",
  "dial_category_idx": 2,
  "confidence_dial": 35,
  "selected_option": null,
  "attempt_num": 2
}
```

Valores permitidos para `event_type`:

- `PROBLEM_SELECT`
- `HINT_REQUEST`
- `ANSWER_SUBMIT`
- `CONFIDENCE_UPDATE`
- `SESSION_RESET`

Mapeo del dial de categoría:

- `0`: All
- `1`: Geometry
- `2`: Combinatorics
- `3`: NumberTheory
- `4`: Algebra

El MCU usa `confidence_dial`. El puente lo transforma en `confidence_pct`
cuando llama al motor o construye telemetría.

### Feedback para el MCU

El puente devuelve una trama validada con:

```json
{
  "schema_v": 1,
  "ack_seq": 42,
  "led_state": "SOCRATIC_NUDGE",
  "rgb": [255, 180, 0],
  "buzz_pattern": "SHORT_DOUBLE",
  "lcd_line1": "Check parity of",
  "lcd_line2": "the corner tiles"
}
```

Estados visuales:

| Estado | Color | Uso |
|---|---|---|
| `IDLE` | Azul | Esperando interacción |
| `THINKING` | Cian | Se procesa una petición |
| `SOCRATIC_NUDGE` | Ámbar | Hay una pista socrática |
| `SOLVED` | Verde | Respuesta correcta |
| `BOTTLENECK_ALERT` | Morado | Intervención procedente de RawTree |

### Motor de Dev 2

La función esperada es:

```python
generate_socratic_hint(
    problem_id,
    event_type,
    attempt_num,
    confidence_pct,
    selected_option=None,
    student_note=None,
    agent_override_nudge=None,
)
```

Dev 1 no debe redefinir la respuesta ni crear campos alternativos. Mientras
Dev 2 no esté listo, utilizar una implementación mock determinista.

## 6. Detalle importante: arquitectura real del Arduino UNO Q

El UNO Q no es un Arduino clásico de un solo procesador:

- El **QRB2210** ejecuta Debian Linux y Python.
- El **STM32U585** ejecuta el sketch Arduino/Zephyr y maneja GPIO en tiempo
  real.
- El canal interno recomendado MCU ↔ Linux es **Arduino Bridge / RPC**,
  proporcionado por `Arduino_RouterBridge` y el servicio `arduino-router`.

Por tanto:

- Mantener el protocolo JSON por líneas para el simulador y un UART externo.
- Para la comunicación interna real del UNO Q, usar Bridge/RPC, por ejemplo
  notificaciones `socraticq_event` desde el MCU y `socraticq_feedback` desde
  Python hacia el MCU.
- No usar `Serial1` como enlace interno MCU ↔ QRB2210. Es para UART externo.

Fuentes oficiales:

- [Manual del UNO Q](https://docs.arduino.cc/tutorials/uno-q/user-manual)
- [Guía Debian del UNO Q](https://docs.arduino.cc/tutorials/uno-q/debian-guide/)
- [SSH en UNO Q](https://docs.arduino.cc/tutorials/uno-q/ssh/)
- [Arduino RouterBridge](https://github.com/arduino-libraries/Arduino_RouterBridge)

## 7. Estado de la placa física

La placa está conectada por USB-C y Windows la detecta correctamente:

- Interfaz ADB: Arduino `VID_2341`, `PID_0078`.
- Puerto serie detectado: `COM5`.
- Identificador ADB actual: `2930722764`.
- Hostname Linux: `uno-q`.
- Usuario Linux: `arduino`.

ADB se instaló mediante `Google.PlatformTools`. Con la instalación actual,
este ejecutable está disponible en una ruta bajo:

```text
%LOCALAPPDATA%\Microsoft\WinGet\Packages\Google.PlatformTools*\platform-tools\adb.exe
```

El script de detección local debe preferirse frente a hardcodear esa ruta.

## 8. SSH: estado y límites actuales

Se ejecutaron y verificaron estas acciones en la placa por ADB:

```bash
arduino-app-cli system network-mode enable
sudo systemctl enable --now ssh
```

Resultado confirmado:

- Network mode: `enabled`
- Servicio SSH: `enabled` y `active`
- Puerto 22 escuchando en IPv4 e IPv6
- SSH persistirá tras reiniciar la placa

La placa todavía no está conectada a una red Wi-Fi:

- `wlan0` aparece desconectada.
- La IP `172.17.0.1` pertenece a `docker0`, una red interna; no sirve para
  entrar desde el PC.

No se conoce, ni se debe solicitar por chat, la contraseña de Linux. Es la que
el propietario creó durante el primer setup del UNO Q.

Para continuar:

1. Completar la conexión Wi-Fi mediante Arduino App Lab o modo SBC.
2. Consultar la IP con ADB:

   ```powershell
   adb -s 2930722764 shell "hostname -I"
   ```

3. Conectar desde el PC, estando ambos equipos en la misma red:

   ```powershell
   ssh arduino@uno-q.local
   ```

   o usar la IP Wi-Fi devuelta por `hostname -I`.

## 9. Verificación ya realizada

- Python local instalado: `Python 3.13.17`.
- Usar el comando `python`; el launcher `py` conserva una referencia rota a
  una instalación de Microsoft Store.
- Los contratos compartidos pasaron correctamente.
- Las pruebas de hardware locales pasaron: 4 pruebas.
- El smoke test del simulador produjo dos tramas válidas: primero
  `THINKING`, después `SOCRATIC_NUDGE`.

No se ha compilado ni flasheado todavía el sketch en la placa.

## 10. Próximos pasos seguros

1. Abrir una terminal nueva y verificar `python --version`.
2. Configurar Wi-Fi en el UNO Q sin compartir contraseñas en mensajes.
3. Confirmar la conexión SSH.
4. Revisar el árbol de trabajo local y ejecutar tests antes de modificarlo.
5. Verificar físicamente el pinout y el cableado antes de flashear.
6. Compilar y cargar el sketch mediante Arduino App Lab.
7. Probar el intercambio Bridge MCU ↔ Python con el motor mock.
8. Integrar el motor real cuando Dev 2 publique su función.
9. Integrar telemetría cuando Dev 3 publique `/ingest`.
10. Añadir una demostración offline: desconectar Wi-Fi, seguir mostrando pistas
    y guardar telemetría en la cola local.

## 11. Precauciones de hardware

No asumir el cableado final. La configuración de ejemplo contempla:

- dial de categoría: `A0`;
- dial de confianza: `A1`;
- botón de pista: `D2` con `INPUT_PULLUP`, conectado a GND;
- botón de envío: `D3` con `INPUT_PULLUP`, conectado a GND;
- LED RGB externo: `D9`, `D10`, `D11` con resistencias;
- buzzer: `D6`.

Antes de conectar componentes:

- comprobar el pinout oficial del UNO Q;
- confirmar la polaridad del LED RGB;
- usar resistencias adecuadas;
- cambiar los pines configurables del sketch solo si el cableado real difiere;
- no flashear código que active salidas hasta haber confirmado el montaje.

## 12. Comandos de diagnóstico útiles

```powershell
# Estado del repo
git status

# Contratos y pruebas hardware
python -m contracts.check_contracts
python -m unittest hardware.tests.test_bridge

# Detectar el UNO Q y ver ADB/puertos
python -m hardware.bridge.detect_unoq --json

# Comprobar/activar SSH mediante ADB, con una sola placa autorizada conectada
python -m hardware.bridge.detect_unoq --enable-ssh --json

# Probar el simulador sin placa
python -m hardware.bridge.mock_mcu --once hint |
  python -m hardware.bridge.serial_daemon --stdin --engine mock --no-ingest
```
