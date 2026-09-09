#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Matteo Dalle Feste

"""Sonda OCPP 1.6J: un CSMS minimo per capire cosa fa davvero la wallbox.

Nessuna dipendenza esterna: handshake WebSocket (RFC 6455) e framing sono
implementati a mano, proprio perche' una delle ipotesi da escludere e' un
problema di libreria/versione. Gira con Python 3.9+ su qualunque macchina
della stessa LAN della wallbox.

Due modalita':

  sniff   Accetta la connessione TCP, stampa per intero la richiesta HTTP di
          upgrade (path, header, subprotocollo, Authorization) e chiude.
          Serve a rispondere alla domanda "la wallbox mi raggiunge?".

  serve   Handshake completo + dialogo OCPP 1.6J, con console interattiva per
          mandare comandi (SetChargingProfile, TriggerMessage, ...).

Esempi:

    python tools/ocpp_probe.py sniff --port 9000
    python tools/ocpp_probe.py serve --port 9000
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import socket
import sys
from datetime import datetime, timezone
from itertools import count

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OCPP_SUBPROTOCOLS = ("ocpp1.6", "ocpp1.5")

CALL, CALLRESULT, CALLERROR = 2, 3, 4

_msg_ids = count(1)
_tx_ids = count(1)


def now_iso() -> str:
    """Timestamp UTC nel formato che l'OCPP si aspetta."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def log(tag: str, msg: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"{stamp} [{tag}] {msg}", flush=True)


def local_ips() -> list[str]:
    """IP locali plausibili, da digitare nell'app della wallbox."""
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # non invia nulla, sceglie solo la rotta
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


# --------------------------------------------------------------------------
# Handshake HTTP
# --------------------------------------------------------------------------

async def read_http_request(
    reader: asyncio.StreamReader, timeout: float = 15.0
) -> tuple[str, dict[str, str], str]:
    """Legge la richiesta di upgrade. Ritorna (request_line, header, testo grezzo)."""
    raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)
    text = raw.decode("utf-8", "replace")
    lines = text.split("\r\n")
    request_line = lines[0] if lines else ""
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
    return request_line, headers, text


def describe_request(request_line: str, headers: dict[str, str]) -> None:
    """Stampa in chiaro le cose che contano per la diagnosi."""
    parts = request_line.split()
    path = parts[1] if len(parts) > 1 else "?"
    log("HTTP", f"richiesta: {request_line}")
    log("HTTP", f"path (di solito contiene il Charge Point ID): {path}")

    offered = headers.get("sec-websocket-protocol", "")
    if offered:
        log("HTTP", f"subprotocolli offerti: {offered}")
    else:
        log("WARN", "nessun header Sec-WebSocket-Protocol: la wallbox non dichiara ocpp1.6")

    auth = headers.get("authorization")
    if auth:
        kind, _, blob = auth.partition(" ")
        if kind.lower() == "basic":
            try:
                user, _, pwd = base64.b64decode(blob).decode("utf-8", "replace").partition(":")
                shown = "(vuota)" if not pwd else "*" * len(pwd)
                log("HTTP", f"basic auth: utente={user!r} password={shown}")
            except Exception:  # noqa: BLE001 - e' solo diagnostica
                log("HTTP", f"Authorization non decodificabile: {auth}")
        else:
            log("HTTP", f"Authorization: {auth}")
    else:
        log("HTTP", "nessuna Authorization (security profile 1, in chiaro)")

    for key in ("host", "user-agent", "sec-websocket-version", "origin"):
        if key in headers:
            log("HTTP", f"{key}: {headers[key]}")


def accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def pick_subprotocol(headers: dict[str, str]) -> str | None:
    offered = [p.strip() for p in headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    for proto in OCPP_SUBPROTOCOLS:
        if proto in offered:
            return proto
    if offered:
        # Non e' quello atteso, ma rispondiamo lo stesso: vogliamo vedere i messaggi.
        log("WARN", f"subprotocollo inatteso, accetto comunque {offered[0]!r}")
        return offered[0]
    return None


# --------------------------------------------------------------------------
# Framing WebSocket (RFC 6455)
# --------------------------------------------------------------------------

class WSConnection:
    """Connessione WebSocket lato server: solo quel che serve a OCPP-J."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.closed = False

    async def _read_exactly(self, n: int) -> bytes:
        return await self.reader.readexactly(n) if n else b""

    async def _read_frame(self) -> tuple[int, bytes, bool]:
        head = await self._read_exactly(2)
        fin = bool(head[0] & 0x80)
        opcode = head[0] & 0x0F
        masked = bool(head[1] & 0x80)
        length = head[1] & 0x7F
        if length == 126:
            length = int.from_bytes(await self._read_exactly(2), "big")
        elif length == 127:
            length = int.from_bytes(await self._read_exactly(8), "big")
        mask = await self._read_exactly(4) if masked else b""
        payload = await self._read_exactly(length)
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        elif opcode in (0x0, 0x1, 0x2):
            log("WARN", "frame dati non mascherato dal client (violazione RFC 6455)")
        return opcode, payload, fin

    async def recv(self) -> str | None:
        """Prossimo messaggio testuale, o None se la connessione si chiude."""
        buffer = bytearray()
        while True:
            try:
                opcode, payload, fin = await self._read_frame()
            except (asyncio.IncompleteReadError, ConnectionResetError):
                self.closed = True
                return None

            if opcode == 0x8:                      # close
                code = int.from_bytes(payload[:2], "big") if len(payload) >= 2 else 1005
                reason = payload[2:].decode("utf-8", "replace")
                log("WS", f"close dal client: code={code} reason={reason!r}")
                await self._send_frame(0x8, payload[:125])
                self.closed = True
                return None
            if opcode == 0x9:                      # ping
                await self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:                      # pong
                continue
            if opcode == 0x2:
                log("WARN", "frame binario ignorato (OCPP-J usa testo)")
                continue

            buffer.extend(payload)
            if fin:
                return buffer.decode("utf-8", "replace")

    async def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self.closed:
            return
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(n)
        elif n < 65536:
            header.append(126)
            header += n.to_bytes(2, "big")
        else:
            header.append(127)
            header += n.to_bytes(8, "big")
        self.writer.write(bytes(header) + payload)
        await self.writer.drain()

    async def send(self, text: str) -> None:
        await self._send_frame(0x1, text.encode("utf-8"))

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                await self._send_frame(0x8, b"\x03\xe8")
                self.writer.close()
            except (ConnectionResetError, BrokenPipeError):
                pass


# --------------------------------------------------------------------------
# Livello OCPP 1.6J
# --------------------------------------------------------------------------

class ChargePointSession:
    """Un dialogo con una wallbox connessa."""

    def __init__(self, ws: WSConnection, path: str, autoget: bool) -> None:
        self.ws = ws
        self.path = path
        self.autoget = autoget
        self.cp_id = path.rstrip("/").rsplit("/", 1)[-1] or "(vuoto)"
        self.pending: dict[str, asyncio.Future] = {}
        self.tx_id: int | None = None
        self.status: str = "?"
        self.boot: dict = {}

    # --- invio ---
    async def call(self, action: str, payload: dict) -> dict | None:
        uid = str(next(_msg_ids))
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[uid] = future
        await self.ws.send(json.dumps([CALL, uid, action, payload]))
        log("-->", f"{action} {json.dumps(payload, separators=(',', ':'))}")
        try:
            return await asyncio.wait_for(future, timeout=30)
        except asyncio.TimeoutError:
            log("ERR", f"nessuna risposta a {action} entro 30s")
            self.pending.pop(uid, None)
            return None

    # --- ricezione ---
    async def run(self) -> None:
        while True:
            raw = await self.ws.recv()
            if raw is None:
                break
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                log("ERR", f"messaggio non JSON: {raw[:200]}")
                continue

            kind = message[0]
            if kind == CALL:
                _, uid, action, payload = message
                log("<--", f"{action} {json.dumps(payload, separators=(',', ':'))}")
                response = await self.handle(action, payload)
                await self.ws.send(json.dumps([CALLRESULT, uid, response]))
            elif kind == CALLRESULT:
                _, uid, payload = message
                log("<==", f"risposta {json.dumps(payload, separators=(',', ':'))}")
                future = self.pending.pop(uid, None)
                if future and not future.done():
                    future.set_result(payload)
            elif kind == CALLERROR:
                _, uid, code, description, details = (list(message) + [None] * 5)[:5]
                log("ERR", f"CALLERROR {code}: {description} {details}")
                future = self.pending.pop(uid, None)
                if future and not future.done():
                    future.set_result(None)

    async def handle(self, action: str, payload: dict) -> dict:
        """Risposte minime ma conformi, per tenere viva la sessione."""
        if action == "BootNotification":
            self.boot = payload
            log(
                "INFO",
                "wallbox identificata: "
                f"{payload.get('chargePointVendor')} / {payload.get('chargePointModel')} "
                f"fw={payload.get('firmwareVersion')}",
            )
            if self.autoget:
                asyncio.create_task(self._probe_capabilities())
            return {"currentTime": now_iso(), "interval": 300, "status": "Accepted"}

        if action == "Heartbeat":
            return {"currentTime": now_iso()}

        if action == "StatusNotification":
            self.status = payload.get("status", "?")
            log(
                "INFO",
                f"connettore {payload.get('connectorId')} -> {self.status} "
                f"(error={payload.get('errorCode')})",
            )
            return {}

        if action == "MeterValues":
            for entry in payload.get("meterValue", []):
                readings = ", ".join(
                    f"{s.get('measurand', 'Energy.Active.Import.Register')}"
                    f"{'.' + s['phase'] if s.get('phase') else ''}"
                    f"={s.get('value')}{s.get('unit', '')}"
                    for s in entry.get("sampledValue", [])
                )
                log("METER", readings)
            return {}

        if action == "StartTransaction":
            self.tx_id = next(_tx_ids)
            log("INFO", f"transazione avviata: id={self.tx_id} meterStart={payload.get('meterStart')}")
            return {"transactionId": self.tx_id, "idTagInfo": {"status": "Accepted"}}

        if action == "StopTransaction":
            log(
                "INFO",
                f"transazione chiusa: meterStop={payload.get('meterStop')} "
                f"reason={payload.get('reason')}",
            )
            self.tx_id = None
            return {"idTagInfo": {"status": "Accepted"}}

        if action == "Authorize":
            return {"idTagInfo": {"status": "Accepted"}}

        if action == "DataTransfer":
            return {"status": "Accepted"}

        if action in ("FirmwareStatusNotification", "DiagnosticsStatusNotification"):
            return {}

        log("WARN", f"azione non gestita: {action}")
        return {}

    async def _probe_capabilities(self) -> None:
        """Dopo il boot, chiede le chiavi che decidono se lo smart charging e' usabile."""
        await asyncio.sleep(2)
        keys = [
            "SupportedFeatureProfiles",
            "NumberOfConnectors",
            "ChargingScheduleAllowedChargingRateUnit",
            "ChargeProfileMaxStackLevel",
            "MaxChargingProfilesInstalled",
            "MeterValueSampleInterval",
            "MeterValuesSampledData",
            "ConnectorSwitch3to1PhaseSupported",
        ]
        result = await self.call("GetConfiguration", {"key": keys})
        if not result:
            return
        for item in result.get("configurationKey", []):
            flag = " (sola lettura)" if item.get("readonly") else ""
            log("CONF", f"{item.get('key')} = {item.get('value')}{flag}")
        unknown = result.get("unknownKey") or []
        if unknown:
            log("CONF", f"chiavi non supportate: {', '.join(unknown)}")

    # --- comandi di prova ---
    async def set_limit(self, amps: float, connector: int, phases: int | None) -> None:
        period: dict = {"startPeriod": 0, "limit": float(amps)}
        if phases:
            period["numberPhases"] = phases
        await self.call(
            "SetChargingProfile",
            {
                "connectorId": connector,
                "csChargingProfiles": {
                    "chargingProfileId": 1,
                    "stackLevel": 0,
                    "chargingProfilePurpose": "TxDefaultProfile",
                    "chargingProfileKind": "Relative",
                    "chargingSchedule": {
                        "chargingRateUnit": "A",
                        "chargingSchedulePeriod": [period],
                    },
                },
            },
        )


HELP = """
Comandi disponibili:
  limit <A> [fasi]   SetChargingProfile a <A> ampere (es. 'limit 10')
  pause              SetChargingProfile a 0 A -> atteso stato SuspendedEVSE
  clear              ClearChargingProfile (rimuove i profili installati)
  get [chiave]       GetConfiguration (senza argomenti: tutte le chiavi)
  set <chiave> <val> ChangeConfiguration
  trigger <msg>      TriggerMessage (es. 'trigger MeterValues')
  remotestop         RemoteStopTransaction sulla transazione in corso
  remotestart <tag>  RemoteStartTransaction con idTag
  reset [soft|hard]  Reset
  status             ultimo stato noto
  help / quit
"""


async def console(get_session) -> None:
    loop = asyncio.get_running_loop()
    print(HELP, flush=True)
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            return
        parts = line.strip().split()
        if not parts:
            continue
        cmd, args = parts[0].lower(), parts[1:]

        if cmd in ("quit", "exit"):
            return
        if cmd == "help":
            print(HELP, flush=True)
            continue

        cp: ChargePointSession | None = get_session()
        if cp is None:
            log("ERR", "nessuna wallbox connessa")
            continue

        try:
            if cmd == "status":
                log("INFO", f"cp={cp.cp_id} stato={cp.status} transazione={cp.tx_id} boot={cp.boot}")
            elif cmd == "limit":
                await cp.set_limit(
                    float(args[0]),
                    connector=1,
                    phases=int(args[1]) if len(args) > 1 else None,
                )
            elif cmd == "pause":
                await cp.set_limit(0, connector=1, phases=None)
            elif cmd == "clear":
                await cp.call("ClearChargingProfile", {})
            elif cmd == "get":
                await cp.call("GetConfiguration", {"key": args} if args else {})
            elif cmd == "set":
                await cp.call("ChangeConfiguration", {"key": args[0], "value": " ".join(args[1:])})
            elif cmd == "trigger":
                await cp.call("TriggerMessage", {"requestedMessage": args[0]})
            elif cmd == "remotestop":
                await cp.call("RemoteStopTransaction", {"transactionId": cp.tx_id or 1})
            elif cmd == "remotestart":
                await cp.call(
                    "RemoteStartTransaction",
                    {"idTag": args[0] if args else "EVBALANCE", "connectorId": 1},
                )
            elif cmd == "reset":
                kind = (args[0] if args else "soft").capitalize()
                await cp.call("Reset", {"type": kind})
            else:
                log("ERR", f"comando sconosciuto: {cmd}")
        except (IndexError, ValueError) as err:
            log("ERR", f"argomenti non validi ({err}). Scrivi 'help'.")


# --------------------------------------------------------------------------
# Modalita'
# --------------------------------------------------------------------------

async def run_sniff(host: str, port: int) -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        log("TCP", f"connessione da {peer}")
        try:
            request_line, headers, raw = await read_http_request(reader)
        except asyncio.TimeoutError:
            log("WARN", "connessione aperta ma nessuna richiesta HTTP entro 15s")
            writer.close()
            return
        except asyncio.IncompleteReadError:
            log("WARN", "connessione chiusa prima di completare la richiesta")
            writer.close()
            return

        describe_request(request_line, headers)
        print("--- richiesta grezza ---", flush=True)
        print(raw.replace("\r\n", "\n").strip(), flush=True)
        print("------------------------", flush=True)
        writer.write(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handler, host, port)
    log("INFO", f"sniff in ascolto su {host}:{port} - in attesa della wallbox")
    async with server:
        await server.serve_forever()


async def run_serve(host: str, port: int, autoget: bool) -> None:
    session: dict[str, ChargePointSession | None] = {"cp": None}

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        log("TCP", f"connessione da {peer}")
        try:
            request_line, headers, _ = await read_http_request(reader)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            log("WARN", "handshake non completato")
            writer.close()
            return

        describe_request(request_line, headers)
        key = headers.get("sec-websocket-key")
        if not key:
            log("ERR", "manca Sec-WebSocket-Key: non e' una richiesta WebSocket")
            writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        proto = pick_subprotocol(headers)
        response = [
            "HTTP/1.1 101 Switching Protocols",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Accept: {accept_key(key)}",
        ]
        if proto:
            response.append(f"Sec-WebSocket-Protocol: {proto}")
        writer.write(("\r\n".join(response) + "\r\n\r\n").encode("ascii"))
        await writer.drain()

        path = request_line.split()[1] if len(request_line.split()) > 1 else "/"
        cp = ChargePointSession(WSConnection(reader, writer), path, autoget)
        session["cp"] = cp
        log("INFO", f"handshake completato (subprotocollo={proto}), Charge Point ID = {cp.cp_id!r}")
        try:
            await cp.run()
        finally:
            log("INFO", "wallbox disconnessa")
            if session["cp"] is cp:
                session["cp"] = None
            writer.close()

    server = await asyncio.start_server(handler, host, port)
    log("INFO", f"CSMS in ascolto su {host}:{port}")
    for ip in local_ips():
        log("INFO", f"URL da configurare nella wallbox: ws://{ip}:{port}/EVBALANCE")
    asyncio.create_task(console(lambda: session["cp"]))
    async with server:
        await server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("mode", choices=["sniff", "serve"])
    parser.add_argument("--host", default="0.0.0.0", help="interfaccia di ascolto")
    parser.add_argument("--port", type=int, default=9000, help="porta di ascolto")
    parser.add_argument(
        "--no-autoget",
        action="store_true",
        help="non interrogare le chiavi di configurazione dopo il BootNotification",
    )
    args = parser.parse_args()

    coro = (
        run_sniff(args.host, args.port)
        if args.mode == "sniff"
        else run_serve(args.host, args.port, autoget=not args.no_autoget)
    )
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        log("INFO", "interrotto")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
