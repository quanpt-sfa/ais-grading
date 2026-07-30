import importlib
import pkgutil
from pathlib import Path
from typing import Dict, List, Type
import logging

from src.grading.base import BaseModuleComparator

logger = logging.getLogger(__name__)

class ModuleRegistry:
    """Registry quản lý và tự động quét (auto-discover) các phân hệ chấm điểm."""
    
    _modules: Dict[str, BaseModuleComparator] = {}

    @classmethod
    def auto_discover(cls):
        """Import tất cả các file trong package src.grading.modules và tự động đăng ký."""
        cls._modules.clear()
        modules_dir = Path(__file__).parent / "modules"
        if not modules_dir.exists():
            logger.warning(f"Thư mục modules chưa tồn tại: {modules_dir}")
            return

        for _, name, _ in pkgutil.iter_modules([str(modules_dir)]):
            module_path = f"src.grading.modules.{name}"
            try:
                mod = importlib.import_module(module_path)
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (isinstance(attr, type) 
                        and issubclass(attr, BaseModuleComparator) 
                        and attr is not BaseModuleComparator):
                        instance = attr()
                        cls._modules[instance.module_id] = instance
                        logger.debug(f"Đã đăng ký module chấm điểm: {instance.module_id} ({instance.display_name})")
            except Exception as e:
                logger.error(f"Lỗi khi load module [{name}]: {e}")

        logger.info(f"Tổng số module chấm điểm khả dụng: {len(cls._modules)} ({list(cls._modules.keys())})")

    @classmethod
    def get_module(cls, module_id: str) -> BaseModuleComparator:
        if module_id not in cls._modules:
            raise KeyError(f"Chưa đăng ký module với ID: '{module_id}'")
        return cls._modules[module_id]

    @classmethod
    def get_all_modules(cls) -> Dict[str, BaseModuleComparator]:
        return dict(cls._modules)

    @classmethod
    def get_active_modules(cls, weights_config: Dict[str, float]) -> List[BaseModuleComparator]:
        """Trả về danh sách các module có trọng số > 0 hoặc được định nghĩa trong config."""
        active = []
        for mid, comp in cls._modules.items():
            # Module được coi là active nếu trọng số > 0 hoặc nếu không set weight thì mặc định check nếu có trong config
            weight = weights_config.get(mid, 0)
            if weight > 0 or mid in weights_config:
                active.append(comp)
        return active
