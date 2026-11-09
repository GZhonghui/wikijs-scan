"""检查资源位置：图片直接放在 images，其他资源直接放在 docs。

流程：仅遍历资源元数据，以最后一个后缀、忽略大小写判断是否属于配置的
图片类型，再比较文件直接父目录的完整名称。目录名来自全局设置，允许
任意深度的同名目录，但资源不能放在该目录的后代子目录中。
页面不参与；只要一个资源位置不符合规则即失败，并记录全部异常路径。
"""

from pathlib import PurePosixPath
import re

from ..models import ScanContext, ScanResult
from ._validation import validate_keys


DEFAULT_IMAGE_EXTENSIONS = ("png", "svg", "jpg", "jpeg", "gif", "webp")


def validate_options(options: dict) -> None:
    validate_keys(options, {"image_extensions"})
    extensions = options.get("image_extensions", list(DEFAULT_IMAGE_EXTENSIONS))
    if not isinstance(extensions, list) or not all(
        isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9]+", value)
        for value in extensions
    ):
        raise ValueError("image_extensions 必须是后缀名称数组，例如 ['png', 'jpg']，不带点号")


def run(context: ScanContext, options: dict) -> ScanResult:
    validate_options(options)
    images = {value.lower() for value in options.get("image_extensions", DEFAULT_IMAGE_EXTENSIONS)}
    errors = []
    for asset in sorted(context.snapshot.assets, key=lambda item: item.path):
        path = PurePosixPath(asset.path)
        expected = (
            context.settings.images_dir
            if path.suffix.lstrip(".").lower() in images
            else context.settings.docs_dir
        )
        if path.parent.name != expected:
            errors.append(f"{asset.path}：应直接放在 {expected}/ 中")
    return ScanResult(not errors, "\n".join(errors))
