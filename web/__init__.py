"""
Web layer for PalmSentinel V2.

``create_app`` is the only entry point.  It owns the wiring, so tests can build an
application around a temporary data directory and a temporary store without
touching a module-level global.
"""

from __future__ import annotations

import os
from typing import Optional

from flask import Flask

import paths

from . import views
from .store import ImageStore

#: 2 GiB.  A 1.4 gigapixel orthomosaic is comfortably inside this; anything
#: larger should be tiled upstream rather than pushed through a browser.
MAX_UPLOAD_BYTES = 2048 * 1024 * 1024


def create_app(
    data_dir: Optional[str] = None,
    gsd_cm: float = 4.0,
    preload: bool = True,
) -> Flask:
    """
    Build the application.

    Read-only assets come from :func:`paths.asset_root` and the imagery library
    from :func:`paths.data_dir`, so the same code serves a source checkout and a
    frozen build without either one knowing which it is.  ``preload`` decodes the
    default orthomosaic during construction; the desktop shell turns this off so
    its window can appear before a 138 MP decode finishes.
    """
    assets = paths.asset_root()
    library = paths.data_dir(data_dir)
    # Make the bundled demo selectable in the library on a first run.
    paths.seed_demo_image(library)

    app = Flask(
        __name__,
        template_folder=os.path.join(assets, "templates"),
        static_folder=os.path.join(assets, "static"),
    )
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.config["JSON_SORT_KEYS"] = False

    store = ImageStore(library, default_gsd_cm=gsd_cm)
    views.store = store

    if preload:
        default = store.default_image()
        if default:
            try:
                store.load(default)
            except Exception as exc:  # pragma: no cover - depends on the asset
                print(f"[PalmSentinel] Could not preload {default}: {exc}")

    app.register_blueprint(views.blueprint)
    app.extensions["palmsentinel_store"] = store
    return app
