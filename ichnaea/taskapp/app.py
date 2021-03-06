"""
Holds global celery application state and startup / shutdown handlers.
"""
from celery import Celery
from celery.app import app_or_default
from celery.signals import (
    beat_init,
    worker_process_init,
    worker_process_shutdown,
    setup_logging,
)

from ichnaea.log import configure_logging
from ichnaea.taskapp.config import (
    configure_celery,
    init_beat,
    init_worker,
    shutdown_worker,
)


@setup_logging.connect
def setup_logging_process(loglevel, logfile, format, colorize, **kwargs):
    """Called at scheduler and worker setup.

    Configures logging using the same configuration as the webapp.
    """
    configure_logging()


@beat_init.connect
def init_beat_process(signal, sender, **kw):
    """
    Called automatically when `celery beat` is started.

    Calls :func:`ichnaea.taskapp.config.init_beat`.
    """
    celery_app = app_or_default()
    init_beat(sender, celery_app)


@worker_process_init.connect
def init_worker_process(signal, sender, **kw):
    """
    Called automatically when `celery worker` is started. This is executed
    inside each forked worker process.

    Calls :func:`ichnaea.taskapp.config.init_worker`.
    """
    # get the app in the current worker process
    celery_app = app_or_default()
    init_worker(celery_app)


@worker_process_shutdown.connect
def shutdown_worker_process(signal, sender, **kw):
    """
    Called automatically when `celery worker` is stopped. This is executed
    inside each forked worker process.

    Calls :func:`ichnaea.taskapp.config.shutdown_worker`.
    """
    celery_app = app_or_default()
    shutdown_worker(celery_app)


celery_app = Celery("ichnaea.taskapp.app")

configure_celery(celery_app)
