"""Errores de dominio del motor; la API los traduce a problem details."""


class EngineError(Exception):
    """Base de los errores que el motor sabe explicar al cliente."""


class InsufficientDataError(EngineError):
    """No hay datos suficientes para producir el resultado pedido."""


class UnknownFeatureError(EngineError):
    """Un override apunta a una feature fuera del catálogo o sin valor en la base."""


class InvalidOverrideError(EngineError):
    """Un override produce un valor que no es un número finito."""
