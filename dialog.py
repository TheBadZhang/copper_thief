"""wxPython dialog for the Copper Thief plugin."""

from __future__ import annotations

import logging
import traceback

import wx

import pcbnew

from . import board_adapter as ba
from . import core
from . import settings as settings_store

log = logging.getLogger(__name__)


class CopperThiefDialog(wx.Dialog):
    def __init__(self, parent, board: pcbnew.BOARD) -> None:
        super().__init__(
            parent,
            title="Copper Thief - 盗铜生成器",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.board = board
        self.settings = settings_store.load_settings()
        self._layer_ids = ba.copper_layer_ids(board)
        self._layer_names = [ba.layer_name(lid) for lid in self._layer_ids]
        self._net_pairs = ba.net_choices(board)
        self._net_codes = [code for code, _ in self._net_pairs]
        self._net_names = [name for _, name in self._net_pairs]
        self._last_plan: core.PlanResult | None = None
        self._last_config: core.PatternConfig | None = None
        self._last_layer_ids: list[int] = []
        self._last_netcode = 0

        self._project_path = settings_store.project_config_path(board)
        project = settings_store.load_project_config(self._project_path)
        self._configs: dict[str, dict] = dict(project.get("configs", {}))
        self._active_config = str(project.get("active", "") or "")
        if not self._active_config:
            self._active_config = next(iter(self._configs), "默认")

        self._build_ui()
        self._load_initial_config()
        self._on_shape_changed(None)
        self._on_net_changed(None)
        self.SetMinSize((780, 760))
        self.Fit()
        self.CentreOnParent()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = wx.BoxSizer(wx.VERTICAL)

        params_static = wx.StaticBox(self, label="阵列参数")
        params_box = wx.StaticBoxSizer(params_static, wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, vgap=6, hgap=10)
        grid.AddGrowableCol(1, 1)

        self.config_choice = wx.ComboBox(
            params_static,
            choices=[],
            style=wx.CB_DROPDOWN,
        )
        self.config_choice.SetToolTip(
            "选择已保存的配置，或直接输入新名称后点“保存配置”。\n"
            "配置保存在 PCB 同目录的 <板名>.copper_thief.json 中。"
        )
        config_row = wx.BoxSizer(wx.HORIZONTAL)
        config_row.Add(self.config_choice, 1, wx.EXPAND)
        self.save_config_button = wx.Button(
            params_static, label="保存配置", style=wx.BU_EXACTFIT
        )
        self.delete_config_button = wx.Button(
            params_static, label="删除", style=wx.BU_EXACTFIT
        )
        config_row.Add(self.save_config_button, 0, wx.LEFT, 4)
        config_row.Add(self.delete_config_button, 0, wx.LEFT, 4)
        self._add_row(grid, "项目配置", config_row, params_static)

        self.config_status = wx.StaticText(params_static, label="")
        grid.Add((0, 0), 0)
        grid.Add(self.config_status, 0, wx.EXPAND)

        self.layer_list = wx.CheckListBox(
            params_static, choices=self._layer_names, size=(-1, 80)
        )
        self._add_row(grid, "铜层", self.layer_list, params_static)

        self.net_choice = wx.Choice(params_static, choices=self._net_names)
        self.net_choice.SetToolTip(
            "盗铜图形所属网络。选择 <无网络> 可避免孤岛铜产生 unconnected DRC 提示。"
        )
        self._add_row(grid, "网络", self.net_choice, params_static)

        self.net_note = wx.StaticText(params_static, label="")
        grid.Add((0, 0), 0)
        grid.Add(self.net_note, 0, wx.EXPAND)

        self.shape_radio = wx.RadioBox(
            params_static,
            label="图案",
            choices=["圆形", "圆角正方形"],
            majorDimension=2,
            style=wx.RA_SPECIFY_COLS,
        )
        self._add_row(grid, "形状", self.shape_radio, params_static)

        self.size_spin = self._spin(params_static, 0.10, 20.0, 1.20, 0.05, "尺寸/直径 (mm)")
        self._add_row(grid, "尺寸 (mm)", self.size_spin, params_static)

        self.radius_spin = self._spin(
            params_static, 0.0, 10.0, 0.20, 0.05, "圆角半径 (mm)"
        )
        self.radius_spin.SetToolTip("仅圆角正方形有效，范围为 0 到边长的一半。")
        self._add_row(grid, "圆角半径 (mm)", self.radius_spin, params_static)

        self.gap_spin = self._spin(params_static, 0.0, 10.0, 0.30, 0.05, "阵列间隙 (mm)")
        self.gap_spin.SetToolTip("相邻盗铜图形边缘之间的净间距，不是中心距。")
        self._add_row(grid, "阵列间隙 (mm)", self.gap_spin, params_static)

        self.copper_spin = self._spin(
            params_static, 0.0, 10.0, 0.20, 0.05, "铜箔间距 (mm)"
        )
        self.copper_spin.SetToolTip(
            "与其它网络铜箔的最小间距。实际值会与板级最小间距、网络类规则取较大者。"
        )
        self._add_row(grid, "铜箔间距 (mm)", self.copper_spin, params_static)

        self.edge_spin = self._spin(params_static, 0.0, 10.0, 0.50, 0.05, "板边间距 (mm)")
        self.edge_spin.SetToolTip(
            "与 Edge.Cuts 的最小间距。实际值会与板级铜到板边规则取较大者。"
        )
        self._add_row(grid, "板边间距 (mm)", self.edge_spin, params_static)

        params_box.Add(grid, 0, wx.EXPAND | wx.ALL, 8)
        root.Add(params_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)

        layout_static = wx.StaticBox(self, label="阵列布局")
        layout_box = wx.StaticBoxSizer(layout_static, wx.VERTICAL)
        layout_grid = wx.FlexGridSizer(cols=2, vgap=6, hgap=10)
        layout_grid.AddGrowableCol(1, 1)

        self.centered_check = wx.CheckBox(layout_static, label="阵列自动居中")
        self.centered_check.SetValue(True)
        layout_grid.Add((0, 0), 0)
        layout_grid.Add(self.centered_check, 0, wx.EXPAND)

        self.offset_x_spin = self._spin(
            layout_static, -100.0, 100.0, 0.0, 0.1, "X 偏移 (mm)"
        )
        self.offset_y_spin = self._spin(
            layout_static, -100.0, 100.0, 0.0, 0.1, "Y 偏移 (mm)"
        )
        self._add_row(layout_grid, "X 偏移 (mm)", self.offset_x_spin, layout_static)
        self._add_row(layout_grid, "Y 偏移 (mm)", self.offset_y_spin, layout_static)

        self.avoid_same_check = wx.CheckBox(layout_static, label="同时避开相同网络的铜箔")
        self.avoid_same_check.SetValue(True)
        self.avoid_same_check.SetToolTip(
            "勾选后，已有同网络铜箔也会被避开；取消后只避开不同网络。"
        )
        layout_grid.Add((0, 0), 0)
        layout_grid.Add(self.avoid_same_check, 0, wx.EXPAND)

        self.replace_check = wx.CheckBox(layout_static, label="生成前删除上一次生成的盗铜")
        self.replace_check.SetValue(True)
        self.replace_check.SetToolTip(
            "勾选：替换现有 CopperThief 组。\n"
            "取消：新图形追加到同一个 CopperThief 组，不产生第二个组。"
        )
        layout_grid.Add((0, 0), 0)
        layout_grid.Add(self.replace_check, 0, wx.EXPAND)

        self.max_count_spin = wx.SpinCtrl(
            layout_static, min=100, max=200000, initial=20000
        )
        self._add_row(layout_grid, "最大尝试数", self.max_count_spin, layout_static)

        layout_box.Add(layout_grid, 0, wx.EXPAND | wx.ALL, 8)
        root.Add(layout_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)

        status_static = wx.StaticBox(self, label="结果")
        status_box = wx.StaticBoxSizer(status_static, wx.VERTICAL)
        self.status = wx.TextCtrl(
            status_static,
            value=(
                "1. 选择铜层、网络和图案参数。\n"
                "2. 点击“预览数量”先检查可放置数量与跳过原因。\n"
                "3. 点击“生成盗铜”写入 PCB。\n\n"
                "提示：实际板边间距与铜箔间距会自动采用板级 DRC 规则中的较大值。"
            ),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
            size=(-1, 100),
        )
        status_box.Add(self.status, 1, wx.EXPAND | wx.ALL, 8)
        root.Add(status_box, 1, wx.EXPAND | wx.ALL, 10)

        button_row = wx.BoxSizer(wx.HORIZONTAL)
        self.preview_button = wx.Button(self, label="预览数量")
        self.generate_button = wx.Button(self, label="生成盗铜")
        self.remove_button = wx.Button(self, label="删除现有盗铜")
        self.diagnose_button = wx.Button(self, label="诊断/导出报告")
        self.cancel_button = wx.Button(self, wx.ID_CANCEL, "关闭")
        button_row.Add(self.remove_button, 0, wx.RIGHT, 8)
        button_row.Add(self.diagnose_button, 0, wx.RIGHT, 8)
        button_row.AddStretchSpacer(1)
        button_row.Add(self.preview_button, 0, wx.RIGHT, 8)
        button_row.Add(self.generate_button, 0, wx.RIGHT, 8)
        button_row.Add(self.cancel_button, 0)
        root.Add(button_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self.SetSizer(root)

        self.config_choice.Bind(wx.EVT_COMBOBOX, self._on_config_selected)
        self.save_config_button.Bind(wx.EVT_BUTTON, self._on_save_config)
        self.delete_config_button.Bind(wx.EVT_BUTTON, self._on_delete_config)
        self.shape_radio.Bind(wx.EVT_RADIOBOX, self._on_shape_changed)
        self.size_spin.Bind(wx.EVT_SPINCTRLDOUBLE, self._on_size_changed)
        self.net_choice.Bind(wx.EVT_CHOICE, self._on_net_changed)
        self.preview_button.Bind(wx.EVT_BUTTON, self._on_preview)
        self.generate_button.Bind(wx.EVT_BUTTON, self._on_generate)
        self.remove_button.Bind(wx.EVT_BUTTON, self._on_remove)
        self.diagnose_button.Bind(wx.EVT_BUTTON, self._on_diagnose)
        self.cancel_button.Bind(wx.EVT_BUTTON, self._on_close)
        self.Bind(wx.EVT_CLOSE, self._on_window_close)

    def _spin(
        self,
        parent,
        minimum: float,
        maximum: float,
        initial: float,
        increment: float,
        tip: str,
    ):
        control = wx.SpinCtrlDouble(
            parent,
            min=minimum,
            max=maximum,
            initial=initial,
            inc=increment,
        )
        control.SetDigits(3)
        control.SetToolTip(tip)
        return control

    def _add_row(self, sizer, label: str, control, parent) -> None:
        sizer.Add(wx.StaticText(parent, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(control, 1, wx.EXPAND)

    # -- settings -----------------------------------------------------------

    def _load_initial_config(self) -> None:
        if self._configs:
            name = self._active_config
            if name not in self._configs:
                name = next(iter(self._configs))
            self._active_config = name
            self._apply_params(self._configs[name])
        else:
            self._apply_params(self.settings)
            self._active_config = "默认"
        self._refresh_config_choice()
        self._update_config_status()

    def _apply_params(self, data: dict) -> None:
        shape = str(data.get("shape", core.SHAPE_CIRCLE))
        self.shape_radio.SetSelection(1 if shape == core.SHAPE_ROUNDED_SQUARE else 0)

        self.size_spin.SetValue(float(data.get("size_mm", 1.20)))
        self.radius_spin.SetValue(float(data.get("corner_radius_mm", 0.20)))
        self.gap_spin.SetValue(float(data.get("gap_mm", 0.30)))
        self.copper_spin.SetValue(float(data.get("copper_clearance_mm", 0.20)))
        self.edge_spin.SetValue(float(data.get("edge_clearance_mm", 0.50)))
        self.centered_check.SetValue(bool(data.get("centered", True)))
        self.offset_x_spin.SetValue(float(data.get("offset_x_mm", 0.0)))
        self.offset_y_spin.SetValue(float(data.get("offset_y_mm", 0.0)))
        self.avoid_same_check.SetValue(bool(data.get("avoid_same_net", True)))
        self.replace_check.SetValue(bool(data.get("replace_existing", True)))
        self.max_count_spin.SetValue(int(data.get("max_count", 20000)))

        selected_layers = set(data.get("layers", ["F.Cu"]))
        for index, name in enumerate(self._layer_names):
            self.layer_list.Check(index, name in selected_layers)
        if not any(self.layer_list.IsChecked(i) for i in range(len(self._layer_names))):
            for index, name in enumerate(self._layer_names):
                if name == "F.Cu":
                    self.layer_list.Check(index, True)

        selected_net = str(data.get("net", "<无网络>"))
        net_index = 0
        for index, name in enumerate(self._net_names):
            if name == selected_net:
                net_index = index
                break
        self.net_choice.SetSelection(net_index)

    def _refresh_config_choice(self, selected: str | None = None) -> None:
        value = selected if selected is not None else self._active_config
        self.config_choice.Set(list(self._configs.keys()))
        self.config_choice.SetValue(value or "默认")

    def _update_config_status(self, extra: str = "") -> None:
        if self._project_path is not None:
            try:
                exists = self._project_path.exists()
            except Exception:
                exists = False
            suffix = "" if exists else " (未创建，保存后生成)"
            label = f"配置文件: {self._project_path.name}{suffix}"
            if extra:
                label += f"  |  {extra}"
            self.config_status.SetLabel(label)
            self.config_status.SetToolTip(str(self._project_path))
        else:
            self.config_status.SetLabel(
                "当前 PCB 尚未保存，项目配置只能暂存到全局设置"
            )
            self.config_status.SetToolTip("")
        self.Layout()

    def _current_config_name(self) -> str:
        return self.config_choice.GetValue().strip() or "默认"

    def _save_project_config(self, notify: bool = True) -> bool:
        name = self._current_config_name()
        if len(name) > 100:
            if notify:
                wx.MessageBox(
                    "配置名称过长（最多 100 个字符）。",
                    "Copper Thief",
                    wx.OK | wx.ICON_WARNING,
                    self,
                )
            return False
        self._configs[name] = self._settings_dict()
        self._active_config = name
        if self._project_path is None:
            self._refresh_config_choice(name)
            self._update_config_status("当前 PCB 未保存")
            if notify:
                wx.MessageBox(
                    "当前 PCB 文件尚未保存，无法在项目目录中创建配置文件。\n"
                    "请先保存 PCB，然后重新打开插件。",
                    "Copper Thief",
                    wx.OK | wx.ICON_INFORMATION,
                    self,
                )
            return False
        ok = settings_store.save_project_config(
            self._project_path,
            {"active": name, "configs": self._configs},
        )
        if ok:
            self._refresh_config_choice(name)
            self._update_config_status(f"已保存 '{name}'")
            settings_store.save_settings(self._settings_dict())
        else:
            self._update_config_status("配置文件写入失败")
            if notify:
                wx.MessageBox(
                    f"无法写入配置文件：\n{self._project_path}\n\n"
                    "请检查目录是否可写。",
                    "Copper Thief",
                    wx.OK | wx.ICON_ERROR,
                    self,
                )
        return ok

    def _on_save_config(self, event) -> None:
        self._save_project_config(notify=True)

    def _on_delete_config(self, event) -> None:
        name = self._current_config_name()
        if name not in self._configs:
            wx.MessageBox(
                f"配置 '{name}' 不存在。",
                "Copper Thief",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        answer = wx.MessageBox(
            f"确定删除配置 '{name}' 吗？",
            "Copper Thief",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
            self,
        )
        if answer != wx.YES:
            return
        del self._configs[name]
        next_name = next(iter(self._configs), "默认")
        self._active_config = next_name
        if next_name in self._configs:
            self._apply_params(self._configs[next_name])
            self._on_shape_changed(None)
            self._on_net_changed(None)
        self._refresh_config_choice(next_name)
        if self._project_path is not None:
            settings_store.save_project_config(
                self._project_path,
                {"active": next_name, "configs": self._configs},
            )
            self._update_config_status(f"已删除 '{name}'")
        else:
            self._update_config_status(f"已删除 '{name}'（仅内存）")

    def _on_config_selected(self, event) -> None:
        name = self.config_choice.GetValue().strip()
        if name not in self._configs:
            self._update_config_status(f"新配置名称: {name or '默认'}，点“保存配置”创建")
            return
        self._active_config = name
        self._apply_params(self._configs[name])
        self._on_shape_changed(None)
        self._on_net_changed(None)
        self._update_config_status(f"已载入 '{name}'")

    def _settings_dict(self) -> dict:
        shape = (
            core.SHAPE_ROUNDED_SQUARE
            if self.shape_radio.GetSelection() == 1
            else core.SHAPE_CIRCLE
        )
        return {
            "shape": shape,
            "size_mm": self.size_spin.GetValue(),
            "corner_radius_mm": self.radius_spin.GetValue(),
            "gap_mm": self.gap_spin.GetValue(),
            "copper_clearance_mm": self.copper_spin.GetValue(),
            "edge_clearance_mm": self.edge_spin.GetValue(),
            "centered": self.centered_check.GetValue(),
            "offset_x_mm": self.offset_x_spin.GetValue(),
            "offset_y_mm": self.offset_y_spin.GetValue(),
            "avoid_same_net": self.avoid_same_check.GetValue(),
            "replace_existing": self.replace_check.GetValue(),
            "max_count": self.max_count_spin.GetValue(),
            "layers": [
                self._layer_names[i]
                for i in range(len(self._layer_names))
                if self.layer_list.IsChecked(i)
            ],
            "net": self._net_names[self.net_choice.GetSelection()]
            if self.net_choice.GetSelection() >= 0
            else "<无网络>",
        }

    # -- events -------------------------------------------------------------

    def _on_shape_changed(self, event) -> None:
        is_square = self.shape_radio.GetSelection() == 1
        self.radius_spin.Enable(is_square)
        if is_square:
            self._sync_radius_limit()
        else:
            self.radius_spin.SetValue(self.size_spin.GetValue() / 2.0)

    def _on_size_changed(self, event) -> None:
        self._sync_radius_limit()

    def _sync_radius_limit(self) -> None:
        if self.shape_radio.GetSelection() != 1:
            return
        half = self.size_spin.GetValue() / 2.0
        if self.radius_spin.GetMax() != half:
            self.radius_spin.SetMax(half)
        if self.radius_spin.GetValue() > half:
            self.radius_spin.SetValue(half)

    def _on_net_changed(self, event) -> None:
        code = self._current_netcode()
        if code:
            self.net_note.SetLabel(
                "注意：孤立盗铜会属于所选网络；KiCad DRC 可能出现 unconnected items，"
                "这是未连接孤岛铜的正常提示。"
            )
        else:
            self.net_note.SetLabel(
                "无网络：适合纯铜平衡/盗铜，不会产生网络未连接提示。"
            )
        self.net_note.Wrap(660)
        self.Layout()

    def _current_netcode(self) -> int:
        selection = self.net_choice.GetSelection()
        if selection < 0 or selection >= len(self._net_codes):
            return 0
        return int(self._net_codes[selection])

    def _on_preview(self, event) -> None:
        self._run(commit=False)

    def _on_generate(self, event) -> None:
        self._run(commit=True)

    def _on_remove(self, event) -> None:
        count = ba.remove_generated(self.board)
        ba.refresh_view()
        self.status.SetValue(f"已删除上一次生成的盗铜图形：{count} 个。")
        self._last_plan = None

    def _on_diagnose(self, event) -> None:
        try:
            config, layer_ids, netcode, _ = self._collect_config()
        except Exception as exc:
            wx.MessageBox(str(exc), "参数错误", wx.OK | wx.ICON_WARNING, self)
            return
        user_clearance = ba.from_mm(self.copper_spin.GetValue())
        config = ba.apply_rule_clearances(self.board, config)
        try:
            wx.BeginBusyCursor()
            try:
                geometry = ba.build_board_geometry(
                    self.board, layer_ids, netcode, user_clearance
                )
            finally:
                wx.EndBusyCursor()
            plan = core.Planner(
                geometry,
                config,
                target_netcode=netcode,
            ).plan()
            report = ba.diagnostic_report(
                self.board, geometry, config, plan, layer_ids
            )
        except Exception as exc:
            log.error("Copper Thief diagnostics failed:\n%s", traceback.format_exc())
            wx.MessageBox(
                f"诊断失败：{exc}",
                "Copper Thief",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return

        path = settings_store.kicad_config_dir() / "copper_thief_diagnostic.txt"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(report, encoding="utf-8")
        except Exception:
            path = None
        log.info("Diagnostics:\n%s", report)
        self.status.SetValue(report)
        if path is not None:
            wx.MessageBox(
                "诊断报告已生成：\n"
                f"{path}\n\n"
                "请把该文件发给我，或把当前“结果”框里的内容截图发给我。",
                "Copper Thief 诊断",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
        else:
            wx.MessageBox(
                "无法写入诊断文件，请把“结果”框里的内容复制/截图发给我。",
                "Copper Thief 诊断",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )

    def _on_close(self, event) -> None:
        try:
            settings_store.save_settings(self._settings_dict())
        except Exception:
            pass
        if self.IsModal():
            self.EndModal(wx.ID_CANCEL)
        else:
            self.Close()

    def _on_window_close(self, event) -> None:
        try:
            settings_store.save_settings(self._settings_dict())
        except Exception:
            pass
        if self.IsModal():
            self.EndModal(wx.ID_CANCEL)
        else:
            self.Destroy()

    # -- planning and committing -------------------------------------------

    def _collect_config(self) -> tuple[core.PatternConfig, list[int], int, str]:
        layer_ids = [
            self._layer_ids[i]
            for i in range(len(self._layer_ids))
            if self.layer_list.IsChecked(i)
        ]
        if not layer_ids:
            raise ValueError("请至少选择一个铜层。")

        netcode = self._current_netcode()
        net_name = (
            self._net_names[self.net_choice.GetSelection()]
            if self.net_choice.GetSelection() >= 0
            else "<无网络>"
        )
        shape = (
            core.SHAPE_ROUNDED_SQUARE
            if self.shape_radio.GetSelection() == 1
            else core.SHAPE_CIRCLE
        )
        size = ba.from_mm(self.size_spin.GetValue())
        radius = ba.from_mm(self.radius_spin.GetValue())
        if shape == core.SHAPE_CIRCLE:
            radius = size // 2
        config = core.PatternConfig(
            shape=shape,
            size=size,
            corner_radius=radius,
            gap=ba.from_mm(self.gap_spin.GetValue()),
            edge_clearance=ba.from_mm(self.edge_spin.GetValue()),
            centered=self.centered_check.GetValue(),
            offset_x=ba.from_mm(self.offset_x_spin.GetValue()),
            offset_y=ba.from_mm(self.offset_y_spin.GetValue()),
            max_count=int(self.max_count_spin.GetValue()),
            avoid_same_net=self.avoid_same_check.GetValue(),
        )
        config.validate()
        return config, layer_ids, netcode, net_name

    def _run(self, commit: bool) -> None:
        try:
            config, layer_ids, netcode, net_name = self._collect_config()
        except Exception as exc:
            wx.MessageBox(str(exc), "参数错误", wx.OK | wx.ICON_WARNING, self)
            return

        user_clearance = ba.from_mm(self.copper_spin.GetValue())
        config = ba.apply_rule_clearances(self.board, config)
        progress_dialog: wx.ProgressDialog | None = None

        def progress(done: int, total: int) -> bool:
            nonlocal progress_dialog
            if progress_dialog is None:
                progress_dialog = wx.ProgressDialog(
                    "Copper Thief",
                    "正在检查可放置位置…",
                    maximum=max(1, total),
                    parent=self,
                    style=wx.PD_APP_MODAL
                    | wx.PD_CAN_ABORT
                    | wx.PD_AUTO_HIDE
                    | wx.PD_ELAPSED_TIME
                    | wx.PD_REMAINING_TIME,
                )
            if done <= 0:
                return True
            keep_going, _ = progress_dialog.Update(
                min(done, total), f"正在检查 {done} / {total} 个位置"
            )
            if done >= total:
                # PD_AUTO_HIDE may report "not continuing" at 100%; that is
                # not a cancellation.
                return True
            return bool(keep_going)

        try:
            wx.BeginBusyCursor()
            try:
                geometry = ba.build_board_geometry(
                    self.board, layer_ids, netcode, user_clearance
                )
            finally:
                wx.EndBusyCursor()

            plan = core.Planner(
                geometry,
                config,
                target_netcode=netcode,
                progress=progress,
            ).plan()
        except core.PlanCancelled:
            self.status.SetValue("已取消计算。PCB 未发生修改。")
            return
        except core.TooManyPlacements as exc:
            wx.MessageBox(
                f"预计需要检查 {exc.requested} 个位置，超过上限 {exc.limit}。\n"
                "请增大阵列间隙、缩小尺寸，或提高“最大尝试数”。",
                "数量过多",
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return
        except Exception as exc:
            log.error("Copper Thief planning failed:\n%s", traceback.format_exc())
            wx.MessageBox(
                f"计算失败：{exc}\n\n请检查板框是否闭合，或查看插件日志。",
                "Copper Thief",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        finally:
            if progress_dialog is not None:
                try:
                    progress_dialog.Hide()
                    progress_dialog.Destroy()
                except Exception:
                    pass

        self._last_plan = plan
        self._last_config = config
        self._last_layer_ids = layer_ids
        self._last_netcode = netcode
        summary = core.summarize_plan(plan)
        log.info("Plan: %s", summary.replace("\n", " | "))
        if netcode:
            summary += (
                "\n\n网络: "
                + net_name
                + "（孤立铜可能使 DRC 报告 unconnected items）"
            )
        else:
            summary += "\n\n网络: <无网络>"

        if not commit:
            self.status.SetValue(summary)
            self._save_project_config(notify=False)
            settings_store.save_settings(self._settings_dict())
            return

        if plan.stats.placed <= 0:
            self.status.SetValue(summary)
            self._save_project_config(notify=False)
            log.info("No placements. %s", summary.replace("\n", " | "))
            stats = plan.stats
            if stats.candidates <= 0:
                hint = (
                    "候选位置为 0：当前尺寸/板边间距下面板框内放不下一个图形。\n"
                    "请减小尺寸、圆角半径或板边间距，并确认 Edge.Cuts 是一个正常闭合的板框。"
                )
            elif stats.skipped_copper >= max(
                stats.skipped_edge, stats.skipped_keepout, stats.skipped_drill
            ):
                hint = (
                    "主要原因是“铜箔”避让：所选铜层几乎被已有走线、焊盘或铺铜覆盖。\n\n"
                    "可以尝试：\n"
                    "1. 换一个没有整片铺铜的铜层；\n"
                    "2. 如果该层就是 GND 铺铜，网络选 GND 并取消勾选"
                    "“同时避开相同网络的铜箔”（盗铜会与铺铜合并，视觉上可能不再是一个个孤岛）；\n"
                    "3. 先在该层铺铜上挖出阵列窗口/禁布区，再生成盗铜。"
                )
            elif stats.skipped_keepout >= max(stats.skipped_edge, stats.skipped_drill):
                hint = (
                    "主要原因是“禁布区”避让：板内 Rule Area / Keepout 太多。\n"
                    "请检查禁布区是否覆盖了可用区域。"
                )
            elif stats.skipped_edge >= stats.skipped_drill:
                hint = (
                    "主要原因是“板边”避让：板框、开槽或挖孔占用了可用区域。\n"
                    "请减小板边间距，或检查 Edge.Cuts 是否有多余线段。"
                )
            else:
                hint = (
                    "主要原因是“钻孔”避让：可用区域内钻孔/安装孔较多。\n"
                    "请减小铜箔间距，或换一个区域/铜层。"
                )
            wx.MessageBox(
                "没有找到可放置的位置。\n\n"
                + summary
                + "\n\n"
                + hint,
                "Copper Thief - 无法放置",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return

        try:
            if self.replace_check.GetValue():
                removed = ba.remove_generated(self.board)
            else:
                removed = 0
            created = ba.create_generated(
                self.board, config, plan.placements, netcode
            )
            ba.refresh_view()
        except Exception as exc:
            log.error("Copper Thief creation failed:\n%s", traceback.format_exc())
            wx.MessageBox(
                f"写入 PCB 失败：{exc}",
                "Copper Thief",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return

        if removed:
            summary += f"\n\n已替换旧盗铜: {removed} 个"
        summary += f"\n已生成: {created} 个"
        saved_config = self._save_project_config(notify=False)
        if saved_config and self._project_path is not None:
            summary += f"\n配置已保存: {self._project_path.name} / {self._current_config_name()}"
        self.status.SetValue(summary)
        settings_store.save_settings(self._settings_dict())

        note = ""
        if netcode:
            note = (
                "\n\n提示：盗铜图形已分配到网络。由于它们是孤立铜岛，"
                "DRC 可能报告 unconnected items；如需 DRC 干净，请选择 <无网络>。"
            )
        wx.MessageBox(
            f"已生成 {created} 个盗铜图形。\n\n{summary}{note}",
            "Copper Thief 完成",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )
        if self.IsModal():
            self.EndModal(wx.ID_OK)
