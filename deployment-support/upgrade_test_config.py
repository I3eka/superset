"""Run upgrade validation against an isolated copy of the metadata database."""

from sqlalchemy.engine import make_url

from superset_config import *  # noqa: F403

SQLALCHEMY_DATABASE_URI = make_url(SQLALCHEMY_DATABASE_URI).set(  # noqa: F405
    database="superset_upgrade_20260920"
).render_as_string(hide_password=False)
CACHE_CONFIG = {"CACHE_TYPE": "SimpleCache"}
DATA_CACHE_CONFIG = CACHE_CONFIG
FILTER_STATE_CACHE_CONFIG = CACHE_CONFIG
EXPLORE_FORM_DATA_CACHE_CONFIG = CACHE_CONFIG
