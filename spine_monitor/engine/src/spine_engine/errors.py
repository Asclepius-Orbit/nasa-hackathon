"""Errores de dominio del motor; la API los traduce a problem details."""


class EngineError(Exception):
    """Base de los errores que el motor sabe explicar al cliente."""


class InsufficientDataError(EngineError):
    """No hay datos suficientes para producir el resultado pedido."""
