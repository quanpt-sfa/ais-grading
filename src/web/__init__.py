from src.web.app import (
    INDEX_HTML,
    app,
    get_config,
    get_db_and_engine,
    save_config,
)
from src.web.admin_routes import install_admin_routes
from src.web.answer_backup_routes import install_answer_backup_routes
from src.web.compat_routes import install_legacy_config_adapter


# Install the richer operational UI first so /answer/restore resolves to the
# complete management page. The existing backup module still owns the POST
# restore implementation.
install_admin_routes(
    app,
    get_config=get_config,
    save_config=save_config,
    get_db_and_engine=get_db_and_engine,
    index_html=INDEX_HTML,
)
install_answer_backup_routes(
    app,
    get_config=get_config,
    save_config=save_config,
    get_db_and_engine=get_db_and_engine,
)
install_legacy_config_adapter(
    app,
    get_config=get_config,
    save_config=save_config,
)

__all__ = ["app"]
