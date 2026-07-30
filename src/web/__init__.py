from src.web.app import app, get_config, get_db_and_engine, save_config
from src.web.answer_backup_routes import install_answer_backup_routes


install_answer_backup_routes(
    app,
    get_config=get_config,
    save_config=save_config,
    get_db_and_engine=get_db_and_engine,
)

__all__ = ["app"]
