"""Commands to the gateway (start_session, stop_session, mark_event, calibrate) and their acks.

The UI drives the protocol step by step through the API; each step is a command that is validated
against `telemetry/command`, stored and published QoS 1 on `topics.cmd(...)`. The gateway answers on
`.../ack`: normally `accepted` and then `completed` (or `failed`); `rejected` is final. With no ack
within `ack_timeout_s` the command becomes `timeout`. Those timers live in memory: at startup `recover`
arms them again from the `sent` commands in the store and rejects `starting` sessions left without one.

What each ack changes in the session:
- start_session: `accepted` -> the session is `running`; `rejected`/`failed`/timeout -> `rejected`;
  `completed` (stop_session or `max_duration_s`) -> the session ends.
- stop_session: `completed` (or `rejected` with `no_session`: the device no longer has it) -> it ends.
- mark_event: `completed` -> the event is added to the session.
- calibrate: `completed` -> the `calibration_id` of the result goes into the stream declaration.

Ending a session sets `ended_at`/`end_reason` and starts the analysis (features, baseline, assess).

Commands only carry the fields of the schema (which allows no others): pseudonymous ids, never
personal data.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from spine_contracts import SCHEMA_VERSION, topics, validation

from monitor.db import Store

_log = logging.getLogger(__name__)

FINAL_STATES = ("completed", "rejected", "failed")


class Publisher(Protocol):
    async def publish(self, topic: str, payload: dict[str, Any]) -> None: ...


class CommandError(Exception):
    """The command cannot be sent (session state, device offline, bus down...)."""

    def __init__(self, message: str, *, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


class Control:
    def __init__(
        self,
        store: Store,
        publisher: Publisher,
        on_session_ended: Callable[[str], Awaitable[None]],
        *,
        ack_timeout_s: float,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        on_session_closed: Callable[[str], None] | None = None,
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._on_session_ended = on_session_ended
        self._on_session_closed = on_session_closed
        self._ack_timeout_s = ack_timeout_s
        self._clock = clock
        self._tasks: set[asyncio.Task[Any]] = set()
        self._timers: dict[str, asyncio.TimerHandle] = {}

    def _now(self) -> str:
        return self._clock().isoformat()

    def _close_session(self, session_id: str, from_states: tuple[str, ...], to_state: str, error: str | None = None) -> bool:
        """Transition to a state no more data is expected in (`ended` or `rejected`); `on_session_closed` hears of it right away.

        Unlike `on_session_ended`, it covers the sessions that never ran. A failure there never stops the protocol.
        """
        if not self._store.transition_session(session_id, from_states, to_state, error):
            return False
        if self._on_session_closed is not None:
            try:
                self._on_session_closed(session_id)
            except Exception:
                _log.exception("on_session_closed failed for session %s", session_id)
        return True

    def spawn(self, coro: Awaitable[Any]) -> None:
        """Runs `coro` in the background: `idle` waits for it and a failure is logged, not lost."""
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            _log.error("background task failed", exc_info=exc)

    async def idle(self) -> None:
        """Waits for the background work (session analysis) to finish. For tests and shutdown."""
        while pending := [t for t in self._tasks if not t.done()]:
            # Awaiting already finished tasks never yields, so only the pending ones are awaited.
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)  # let the done callbacks (logging, bookkeeping) run

    def close(self) -> None:
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()

    def recover(self) -> None:
        """Arms again the ack timeouts lost in a restart. Once at startup, in the running loop, before consuming acks.

        A `sent` command past its timeout expires now (through `_expire`, so a late `accepted` still revives
        its session); one within it waits only the rest (at most `ack_timeout_s`, if the clock went back).
        A `starting` session left with no `sent` start_session would block its device forever: it is rejected.
        """
        loop = asyncio.get_running_loop()
        now = self._clock()
        expired = rearmed = 0
        for command in self._store.commands_in_state("sent"):
            command_id = command.payload["command_id"]
            elapsed = (now - datetime.fromisoformat(command.payload["issued_at"])).total_seconds()
            if elapsed >= self._ack_timeout_s:
                self._expire(command_id)
                expired += 1
            else:
                delay = min(self._ack_timeout_s - elapsed, self._ack_timeout_s)
                self._timers[command_id] = loop.call_later(delay, self._expire, command_id)
                rearmed += 1
        orphans = 0
        for session in self._store.sessions_in_states(("starting",)):
            session_id = session.payload["session_id"]
            pending = any(c.state == "sent" for c in self._store.session_commands(session_id, "start_session"))
            if not pending and self._close_session(session_id, ("starting",), "rejected", "ack_timeout"):
                orphans += 1
        _log.info("recovered commands: %d expired, %d waiting for their ack; %d starting sessions rejected", expired, rearmed, orphans)

    # --- sending -----------------------------------------------------------------------------------

    def _command(self, astronaut_id: str, device_id: str, kind: str, **fields: Any) -> dict[str, Any]:
        command = {
            "schema_version": SCHEMA_VERSION,
            "command_id": str(uuid.uuid4()),
            "astronaut_id": astronaut_id,
            "device_id": device_id,
            "issued_at": self._now(),
            "type": kind,
            **fields,
        }
        validation.validate("telemetry/command", command)
        return command

    async def _send(self, command: dict[str, Any]) -> None:
        self._store.insert_command(command, "sent")
        try:
            await self._publisher.publish(topics.cmd(command["astronaut_id"], command["device_id"]), command)
        except Exception as exc:
            self._store.set_command_state(command["command_id"], "send_failed")
            if command["type"] == "start_session":
                self._close_session(command["session_id"], ("starting",), "rejected", "bus_unavailable")
            raise CommandError(f"MQTT bus unavailable: {exc}", status=503) from exc
        loop = asyncio.get_running_loop()
        self._timers[command["command_id"]] = loop.call_later(self._ack_timeout_s, self._expire, command["command_id"])

    def _expire(self, command_id: str) -> None:
        self._timers.pop(command_id, None)
        if not self._store.set_command_state(command_id, "timeout", only_if=("sent",)):
            return
        command = self._store.command(command_id)
        _log.warning("command %s (%s) got no ack in %.1f s", command_id, command.type if command else "?", self._ack_timeout_s)
        if command is not None and command.type == "start_session":
            self._close_session(command.payload["session_id"], ("starting",), "rejected", "ack_timeout")

    def _device_online(self, astronaut_id: str, device_id: str) -> None:
        status = self._store.device_status(astronaut_id, device_id)
        if status is not None and status["status"] == "offline":
            raise CommandError(f"device {device_id} is offline")

    def _active(self, session_id: str) -> dict[str, Any]:
        row = self._store.session(session_id)
        if row is None:
            raise CommandError(f"unknown session {session_id}", status=404)
        if row.state != "running":
            raise CommandError(f"session {session_id} is {row.state}, not running")
        return row.payload

    async def start_session(self, session: dict[str, Any], *, max_duration_s: int | None = None) -> dict[str, Any]:
        """`session` is the full entity (already validated against domain/session). Returns the command."""
        astronaut_id, device_id = session["astronaut_id"], session["device_id"]
        self._device_online(astronaut_id, device_id)
        if (active := self._store.active_session(astronaut_id, device_id)) is not None:
            raise CommandError(f"session {active.payload['session_id']} is already {active.state} on {device_id}")
        fields: dict[str, Any] = {
            "session_id": session["session_id"],
            "session_type": session["session_type"],
            "protocol_id": session["protocol_id"],
            "stream_ids": [s["stream_id"] for s in session["streams"]],
        }
        if max_duration_s is not None:
            fields["max_duration_s"] = max_duration_s
        command = self._command(astronaut_id, device_id, "start_session", **fields)
        self._store.insert_session(session, "starting")
        await self._send(command)
        return command

    async def stop_session(self, session_id: str, reason: str, rep_index: int | None = None) -> dict[str, Any]:
        """A stop for pain (`aborted_pain`) is also recorded on the session as a `pain_reported` event, at the instant of the
        command and with `rep_index` when given: the backend keeps it whatever the device answers."""
        session = self._active(session_id)
        command = self._command(session["astronaut_id"], session["device_id"], "stop_session", session_id=session_id, reason=reason)
        if reason == "aborted_pain":
            # Before the send: a bus that is down or an ack that races the await must not lose the pain.
            event = {"session_id": session_id, "event_type": "pain_reported", "issued_at": command["issued_at"]}
            self._add_event(event if rep_index is None else {**event, "rep_index": rep_index})
        await self._send(command)
        return command

    async def mark_event(
        self, session_id: str, event_type: str, rep_index: int | None = None, note: str | None = None, set_index: int | None = None
    ) -> dict[str, Any]:
        session = self._active(session_id)
        fields: dict[str, Any] = {"session_id": session_id, "event_type": event_type}
        if set_index is not None:
            fields["set_index"] = set_index
        if rep_index is not None:
            fields["rep_index"] = rep_index
        if note is not None:
            fields["note"] = note
        command = self._command(session["astronaut_id"], session["device_id"], "mark_event", **fields)
        await self._send(command)
        return command

    async def calibrate(self, session_id: str, calibration_type: str, stream_ids: list[str], duration_s: float | None = None) -> dict[str, Any]:
        session = self._active(session_id)
        declared = {s["stream_id"] for s in session["streams"]}
        if unknown := sorted(set(stream_ids) - declared):
            raise CommandError(f"streams not declared in the session: {unknown}", status=422)
        fields: dict[str, Any] = {"session_id": session_id, "calibration_type": calibration_type, "stream_ids": stream_ids}
        if duration_s is not None:
            fields["duration_s"] = duration_s
        command = self._command(session["astronaut_id"], session["device_id"], "calibrate", **fields)
        await self._send(command)
        return command

    # --- acks ------------------------------------------------------------------------------------

    def on_ack(self, ack: dict[str, Any]) -> None:
        """`ack` is already validated against telemetry/command-ack and matches its topic."""
        command = self._store.command(ack["command_id"])
        if command is None:
            _log.info("ack for unknown command %s (%s)", ack["command_id"], ack["status"])
            return
        if (command.payload["astronaut_id"], command.payload["device_id"]) != (ack["astronaut_id"], ack["device_id"]):
            _log.warning("ack %s comes from another device than its command; ignored", ack["command_id"])
            return
        if not self._store.add_ack(ack):
            return  # redelivery
        if (timer := self._timers.pop(ack["command_id"], None)) is not None:
            timer.cancel()
        status = ack["status"]
        if status in FINAL_STATES:
            self._store.set_command_state(ack["command_id"], status)
        else:
            self._store.set_command_state(ack["command_id"], status, only_if=("sent", "timeout"))

        payload = command.payload
        match command.type, status:
            case "start_session", "accepted":
                self._accept_start(payload["session_id"])
            case "start_session", "rejected" | "failed":
                code = ack.get("error", {}).get("code", status)
                self._close_session(payload["session_id"], ("starting", "running"), "rejected", code)
            case "start_session", "completed":
                self._end(payload["session_id"], ack["timestamp"])
            case "stop_session", "completed":
                self._end(payload["session_id"], ack["timestamp"], payload["reason"])
            case "stop_session", "rejected" if ack.get("error", {}).get("code") == "no_session":
                self._end(payload["session_id"], ack["timestamp"], payload["reason"])
            case "mark_event", "completed":
                self._add_event(payload)
            case "calibrate", "completed":
                if (calibration_id := ack.get("result", {}).get("calibration_id")) is not None:
                    self._link_calibration_from_ack(payload, calibration_id)
            case _:
                pass

    def _accept_start(self, session_id: str) -> None:
        if self._store.transition_session(session_id, ("starting",), "running"):
            return
        row = self._store.session(session_id)
        if row is not None and row.state == "rejected" and row.error == "ack_timeout":
            # The ack came late, but the gateway did start: the session goes on.
            self._store.transition_session(session_id, ("rejected",), "running")

    def _end(self, session_id: str, ended_at: str, reason: str | None = None) -> None:
        row = self._store.session(session_id)
        if row is None or row.state not in ("starting", "running"):
            return
        if reason is None:
            # The start_session ended: the reason of the stop_session if there was one, else max_duration_s.
            stops = self._store.session_commands(session_id, "stop_session")
            reason = stops[-1].payload["reason"] if stops else "completed"
        session = {**row.payload, "ended_at": ended_at, "end_reason": reason}
        self._store.update_session(session_id, session)
        if self._close_session(session_id, ("starting", "running"), "ended"):
            self.spawn(self._on_session_ended(session_id))

    def _add_event(self, command: dict[str, Any]) -> None:
        row = self._store.session(command["session_id"])
        if row is None:
            return
        event = {"event_type": command["event_type"], "timestamp": command["issued_at"]}
        for key in ("set_index", "rep_index", "note"):
            if key in command:
                event[key] = command[key]
        events = [*row.payload.get("events", []), event]
        events.sort(key=lambda e: datetime.fromisoformat(e["timestamp"]))
        self._store.update_session(command["session_id"], {**row.payload, "events": events})

    def _link_calibration_from_ack(self, command: dict[str, Any], calibration_id: str) -> None:
        known = self._store.calibrations({calibration_id})
        stream_id = known[0]["stream_id"] if known else (command["stream_ids"][0] if len(command["stream_ids"]) == 1 else None)
        if stream_id is not None:
            self.link_calibration(command["session_id"], stream_id, calibration_id)

    def link_calibration(self, session_id: str, stream_id: str, calibration_id: str) -> bool:
        """Sets `calibration_id` on the stream declaration of a running session."""
        row = self._store.session(session_id)
        if row is None or row.state not in ("starting", "running"):
            return False
        streams = [
            {**s, "calibration_id": calibration_id} if s["stream_id"] == stream_id else s for s in row.payload["streams"]
        ]
        if streams == row.payload["streams"]:
            return False
        self._store.update_session(session_id, {**row.payload, "streams": streams})
        return True
