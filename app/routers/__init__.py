"""HTTP routers.

Each module here exports a FastAPI ``APIRouter`` that `app.main` wires
into the root app via ``include_router``. Keeping endpoints grouped by
domain lets each file stay under a few hundred lines.

Migration status (tracked in #35):
- ``health``  ✓ moved
- ``auth``    ✓ moved
- ``media``   ✓ moved
- ``articles``   still in app.main
- ``jobs``       still in app.main
- ``admin``      (folded into ``health`` for now)
"""
