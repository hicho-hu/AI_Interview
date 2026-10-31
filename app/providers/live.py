"""Request-local progress hooks; providers never depend on HTTP or business services."""

from contextvars import ContextVar

live_sink = ContextVar("live_answer_sink", default=None)
audio_turn = ContextVar("live_audio_turn", default=None)


def publish(kind, **payload):
    sink = live_sink.get()
    if sink:
        sink({"type": kind, **payload})
