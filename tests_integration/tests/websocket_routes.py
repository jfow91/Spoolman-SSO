"""Every websocket route in spoolman/api/v1, with the kind of change that fires it.

Websockets are not in the OpenAPI document, so the isolation sweep cannot discover them. The unit
test tests/test_websocket_routes.py fails when this list and the v1 app disagree. No imports here:
that unit test loads this module without the integration conftest.
"""

WEBSOCKETS = [
    ("/", "vendor"),
    ("/vendor", "vendor"),
    ("/vendor/{vendor_id}", "vendor"),
    ("/filament", "filament"),
    ("/filament/{filament_id}", "filament"),
    ("/spool", "spool"),
    ("/spool/{spool_id}", "spool"),
    ("/setting", "setting"),
    ("/setting/{key}", "setting"),
    ("/tag/scan", "scan"),
    ("/tag/scan/{reader_id}", "scan"),
]
