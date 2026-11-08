"""Pure builders of the UI views (`app/ui-*.schema.json`): no I/O, no store, no global clock.

Every builder receives the entities it needs (already read from the store) and the instant it should
consider "now" as a parameter, and returns a JSON-ready dict that validates against its schema.
Absence is an explicit null or 'nodata', never a reassuring default.
"""
