"""Fixtures compartidas de los tests del gateway."""

import pytest
from spine_contracts import Muscle, Side
from spine_gateway.settings import GatewaySettings


@pytest.fixture(scope="session")
def settings() -> GatewaySettings:
    """Ajustes del hardware de referencia (Arduino Uno, 10 bits a 5 V, Rs 10 kΩ, MPU-6050 a ±4 g / ±500 °/s).

    Todos los campos que usa la lógica pura van explícitos: los argumentos de inicialización ganan a las variables
    SPINE_GATEWAY_* del entorno y ``_env_file=None`` ignora el ``.env`` del directorio actual. De sesión para poder
    usarla en tests de Hypothesis; quien necesite otro valor hace ``settings.model_copy(update=...)``.
    """
    return GatewaySettings(
        _env_file=None,
        astronaut_id="AST-01",
        device_id="wb-01",
        emg_stream_id="emg_lat_left",
        imu_stream_id="imu_l3",
        stretch_stream_id="stretch_lumbar",
        emg_muscle=Muscle.LATISSIMUS_DORSI,
        emg_side=Side.LEFT,
        adc_bits=10,
        vref_v=5.0,
        emg_input_pin="A0",
        stretch_input_pin="A1",
        stretch_series_resistor_ohm=10000.0,
        imu_accel_range_g=4,
        imu_gyro_range_dps=500,
        imu_dlpf_hz=44,
        emg_gain=1.0,
        stretch_rest_length_mm=100.0,
        emg_mvc_window_ms=250,
    )
