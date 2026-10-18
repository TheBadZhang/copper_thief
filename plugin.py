"""KiCad ActionPlugin entry point."""

from __future__ import annotations

import logging
from pathlib import Path

import pcbnew

from . import settings as settings_store

log = logging.getLogger(__name__)
_ICON = Path(__file__).with_name("icon.png")


class CopperThiefPlugin(pcbnew.ActionPlugin):
    def defaults(self) -> None:
        self.name = "Copper Thief (盗铜)"
        self.category = "Modify PCB"
        self.description = (
            "在 Edge.Cuts 内生成圆形或圆角正方形盗铜阵列，"
            "自动避让已有铜箔、板边、禁布区和钻孔；"
            "支持项目内多套命名配置，生成项统一放入 CopperThief 组。"
        )
        self.show_toolbar_button = True
        self.icon_file_name = str(_ICON)

    def Run(self) -> None:
        import wx

        try:
            _configure_logging()
        except Exception:
            pass

        board = pcbnew.GetBoard()
        if board is None:
            wx.MessageBox(
                "请先打开一个 PCB 文件。",
                "Copper Thief",
                wx.OK | wx.ICON_INFORMATION,
            )
            return

        parent = None
        try:
            parent = wx.GetApp().GetTopWindow()
        except Exception:
            pass

        try:
            from .dialog import CopperThiefDialog

            dialog = CopperThiefDialog(parent, board)
            try:
                dialog.ShowModal()
            finally:
                dialog.Destroy()
        except Exception as exc:
            log.error("Copper Thief failed to start", exc_info=True)
            wx.MessageBox(
                f"Copper Thief 启动失败：{exc}",
                "Copper Thief",
                wx.OK | wx.ICON_ERROR,
            )


def _configure_logging() -> None:
    package_logger = logging.getLogger(__package__ or "copper_thief")
    if package_logger.handlers:
        return
    path = settings_store.kicad_config_dir() / "copper_thief.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO)
