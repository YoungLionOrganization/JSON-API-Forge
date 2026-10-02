"""cPanel / Phusion Passenger entrypoint.

Passenger is normally WSGI-oriented. a2wsgi bridges this FastAPI ASGI app to WSGI.
For WebSockets and other ASGI-only features, use a host that can run Uvicorn/Hypercorn
behind a reverse proxy instead of Passenger WSGI mode.
"""

from framework.wsgi import PassengerApplication

application = PassengerApplication()
