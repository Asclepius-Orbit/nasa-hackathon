"""Puntuación de desviación: cuántas escalas típicas se aleja un valor de su baseline."""

from spine_contracts import BaselineEntry

# MAD -> desviación típica equivalente para datos normales.
MAD_TO_SIGMA = 1.4826
_ABSOLUTE_FLOOR = 1e-6


class RobustZScorer:
    """z robusto: (valor - mediana) / (1.4826 * MAD).

    Si la MAD es ~0 (pocas sesiones, o todas iguales) usa la desviación típica; si también es ~0 usa
    un suelo proporcional a la mediana (`relative_floor`), para no dividir por cero ni disparar un z
    infinito con una baseline de una sola sesión.
    """

    def __init__(self, relative_floor: float = 0.05) -> None:
        self._relative_floor = relative_floor

    def score(self, value: float, entry: BaselineEntry) -> float:
        return (value - entry.median) / self._scale(entry)

    def _scale(self, entry: BaselineEntry) -> float:
        for scale in (MAD_TO_SIGMA * entry.mad, entry.std):
            if scale > _ABSOLUTE_FLOOR:
                return scale
        return max(self._relative_floor * abs(entry.median), _ABSOLUTE_FLOOR)
