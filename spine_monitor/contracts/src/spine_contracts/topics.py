"""Topics MQTT. IMU y estiramiento van en spine; el EMG en emg."""


def spine_raw(crew_id: str) -> str:
    return f"crew/{crew_id}/spine/raw"


def emg_raw(crew_id: str) -> str:
    return f"crew/{crew_id}/emg/raw"


def device_status(crew_id: str) -> str:
    return f"crew/{crew_id}/device/status"
