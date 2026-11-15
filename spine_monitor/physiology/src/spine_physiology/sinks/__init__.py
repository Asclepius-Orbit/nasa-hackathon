"""Destinations for the core's `Envelope`s: JSONL on disk, MQTT and emission pacing.

Sinks never change the payloads: same seed => same payloads whatever the sink or speed.
"""
