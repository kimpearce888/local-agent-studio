"""S4 canvas and list views.

The canvas draws cards in execution order, six per row, with sequence
edges (thin, arrowed) and data-dependency edges (accent, labelled with
the handoff they carry).  The list view is the accessible equivalent
(spec 9): an ordered table with the full card name, Move up / Move
down and the same keyboard actions.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from las.app.gui.s4_studio import EXECUTABLE, GATE_LIKE, TYPE_ICONS
from las.app.gui.widgets import Dialog

CARD_W, CARD_H = 168, 96
GAP_X, GAP_Y = 36, 34
PER_ROW = 6


def accessible_name(studio, task, index: int, total: int,
                    status: str = "") -> str:
    """Full card name (spec 9): `Task 3 of 8: title, status, uses …`."""
    bits = [f"Task {index + 1} of {total}: {task.title}"]
    if status:
        bits.append(status)
    if task.needs:
        bits.append("uses " + " and ".join(task.needs))
    flags = studio.flags_map().get(task.id, (False, False, False))
    bits.append(f"risk flags {sum(flags)} of 3")
    return ", ".join(bits)


class CanvasView(tk.Canvas):
    """The plan canvas (spec S4)."""

    def __init__(self, parent, studio):
        super().__init__(parent, background=studio.theme.c["bg"],
                         highlightthickness=0)
        self.studio = studio
        self.theme = studio.theme
        self._cards: dict[str, dict] = {}      # task id -> item ids
        self._positions: dict[str, tuple[int, int]] = {}
        self._drag = None
        self._link_drag = None
        self._status: dict[str, str] = {}      # task id -> status (runs)
        self._handoffs: dict[str, str] = {}    # task id -> handoff summary
        self._build_bindings()

    def _build_bindings(self) -> None:
        self.bind("<Button-1>", self._on_click)
        self.bind("<Double-Button-1>", self._on_double_click)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<Button-3>", self._on_right_click)
        self.bind("<Delete>", lambda e: self._delete_selected())
        self.bind("<Return>", lambda e: self.studio.inspector.focus_tab())
        for key in ("<Left>", "<Right>", "<Up>", "<Down>"):
            self.bind(key, self._on_arrow)
        self.bind("<Alt-Up>", lambda e: self._move_selected(-1))
        self.bind("<Alt-Down>", lambda e: self._move_selected(1))
        self.bind("<l>", self._link_picker)
        self.bind("<L>", self._link_picker)
        self.configure(takefocus=True)
        self.focus_set()

    # -- geometry -----------------------------------------------------------

    def _layout(self) -> None:
        plan = self.studio.plan
        self._positions.clear()
        if plan is None:
            return
        for i, task in enumerate(plan.tasks):
            row, col = divmod(i, PER_ROW)
            x = 24 + col * (CARD_W + GAP_X)
            y = 24 + row * (CARD_H + GAP_Y)
            self._positions[task.id] = (x, y)

    def render(self, relayout: bool = False) -> None:
        self.delete("all")
        self._cards.clear()
        self._layout()
        plan = self.studio.plan
        if plan is None or not plan.tasks:
            self._empty_hint()
            return
        z = self.studio.zoom
        flags = self.studio.flags_map()
        c = self.theme.c
        # Edges first so cards sit on top.
        for i, task in enumerate(plan.tasks):
            x1, y1 = self._positions[task.id]
            if i + 1 < len(plan.tasks):                 # sequence edge
                nxt = plan.tasks[i + 1]
                x2, y2 = self._positions[nxt.id]
                self._sequence_edge(x1, y1, x2, y2, task.id, nxt.id, z)
            for dep in task.needs:                      # data edge
                if dep in self._positions:
                    x0, y0 = self._positions[dep]
                    self._data_edge(x0, y0, x1, y1, dep, task.id, z)
        # Cards.
        for i, task in enumerate(plan.tasks):
            self._render_card(i, task, flags.get(task.id, (0, 0, 0)), z)
        total_w = 24 + PER_ROW * (CARD_W + GAP_X)
        rows = (len(plan.tasks) + PER_ROW - 1) // PER_ROW
        total_h = 24 + rows * (CARD_H + GAP_Y)
        self.configure(scrollregion=(0, 0, total_w * z, total_h * z))
        if self.studio.show_minimap:
            self._render_minimap(total_w, total_h)

    def _empty_hint(self) -> None:
        c = self.theme.c
        self.create_text(
            (self.winfo_width() or 600) // 2, 120,
            text="Add your first task, or go back and press Plan.",
            fill=c["muted"], font=self.theme.font(2))

    def _scaled(self, v: float) -> float:
        return v * self.studio.zoom

    def _sequence_edge(self, x1, y1, x2, y2, a, b, z) -> None:
        c = self.theme.c
        ax, ay = self._scaled(x1 + CARD_W), self._scaled(y1 + CARD_H / 2)
        bx, by = self._scaled(x2), self._scaled(y2 + CARD_H / 2)
        selected = self.studio.selected in (a, b)
        if selected:
            color, width = c["accent"], 2
        else:
            color, width = c["border"], 1
        self.create_line(ax, ay, bx, by, fill=color, width=width,
                         arrow=tk.LAST, tags=("edge", f"edge-{a}-{b}"))

    def _data_edge(self, x0, y0, x1, y1, a, b, z) -> None:
        c = self.theme.c
        ax, ay = self._scaled(x0 + CARD_W), self._scaled(y0 + CARD_H / 2)
        bx, by = self._scaled(x1), self._scaled(y1 + CARD_H / 2)
        selected = a == self.studio.selected or b == self.studio.selected
        color = c["info"] if selected else c["muted"]
        self.create_line(ax, ay, bx, by, fill=color,
                         width=2 if selected else 1, dash=(4, 2),
                         arrow=tk.LAST, tags=("edge", f"data-{a}-{b}"))
        label = self._handoffs.get(a, a)
        self.create_text((ax + bx) / 2, (ay + by) / 2 - 8,
                         text=label[:24], fill=c["muted"],
                         font=self.theme.font(-3),
                         tags=("edge", f"dlab-{a}-{b}"))

    def _render_card(self, index: int, task, flags, z) -> None:
        c = self.theme.c
        x, y = self._positions[task.id]
        x, y = self._scaled(x), self._scaled(y)
        w, h = self._scaled(CARD_W), self._scaled(CARD_H)
        selected = task.id == self.studio.selected
        bg = c["surface"] if not selected else c["accent_soft"]
        outline = c["accent"] if selected else c["border"]
        if task.type == "gate":
            bg, outline = c["warning_soft"], c["warning"]
        box = self.create_rectangle(x, y, x + w, y + h, fill=bg,
                                    outline=outline, width=2 if selected else 1,
                                    tags=(f"card:{task.id}", "card"))
        # Order number and type icon
        self.create_text(x + 12, y + 14, text=str(index + 1),
                         fill=c["accent"] if selected else c["muted"],
                         font=self.theme.font(0, "bold"),
                         tags=(f"card:{task.id}", "card"))
        icon = TYPE_ICONS.get(task.type, "▶")
        if task.type == "gate" or task.gate_before:
            icon = "⚑ " + icon
        self.create_text(x + w - 14, y + 14, text=icon, fill=c["muted"],
                         font=self.theme.font(-1), tags=(f"card:{task.id}",
                                                         "card"))
        # Title (two lines at most)
        title = task.title if len(task.title) <= 34 else task.title[:33] + "…"
        self.create_text(x + 12, y + 30, text=title, anchor="w", fill=c["text"],
                         font=self.theme.font(0, "bold"), width=int(w - 24),
                         tags=(f"card:{task.id}", "card"))
        # Status icon during runs
        status = self._status.get(task.id)
        if status:
            sicon, stext, _role = self.theme.status_appearance(status)
            self.create_text(x + 12, y + h - 12, text=f"{sicon} {stext}",
                             anchor="w", fill=c["muted"],
                             font=self.theme.font(-2),
                             tags=(f"card:{task.id}", "card"))
        # Capability badge mini + tool count + model chip
        filled = sum(flags)
        badge = " ".join(("A" if flags[0] else "·") +
                         ("B" if flags[1] else "·") +
                         ("C" if flags[2] else "·"))
        # show as letters row
        badge = f"{'A' if flags[0] else '-'}{'B' if flags[1] else '-'}{'C' if flags[2] else '-'}"
        if filled == 3:
            badge += " needs gate"
        self.create_text(x + 12, y + h - 28, text=badge, anchor="w",
                         fill=c["danger"] if filled == 3 else c["muted"],
                         font=self.theme.font(-2, "bold"),
                         tags=(f"card:{task.id}", "card"))
        chips = [f"🛠 {len(task.tools)}".replace("🛠", "tools")]
        if task.model and task.model.get("model_id"):
            chips.append(task.model["model_id"][:12])
        self.create_text(x + w - 10, y + h - 12, text=" · ".join(chips),
                         anchor="e", fill=c["muted"],
                         font=self.theme.font(-3),
                         tags=(f"card:{task.id}", "card"))
        # Ports: output right, input left
        pr = 5
        self.create_oval(x + w - pr - 2, y + h / 2 - pr, x + w + pr - 2,
                         y + h / 2 + pr, fill=c["info"], outline="",
                         tags=(f"port-out:{task.id}", "port"))
        self.create_oval(x - pr + 2, y + h / 2 - pr, x + pr + 2,
                         y + h / 2 + pr, fill=c["muted"], outline="",
                         tags=(f"port-in:{task.id}", "port"))
        # Accessible name is available for screen-reader toolkits via the
        # tag binding data; the list view carries it as row text (spec 9).
        self.itemconfigure(box, tags=(f"card:{task.id}", "card"))

    def _render_minimap(self, total_w: int, total_h: int) -> None:
        c = self.theme.c
        w, h = 120, 70
        x0 = (self.winfo_width() or 800) - w - 12
        y0 = 12
        self.create_rectangle(x0, y0, x0 + w, y0 + h, fill=c["surface2"],
                              outline=c["border"], tags="minimap")
        plan = self.studio.plan
        if not plan or not total_w or not total_h:
            return
        sx, sy = w / total_w, h / total_h
        for i, task in enumerate(plan.tasks):
            tx, ty = self._positions[task.id]
            mx = x0 + tx * sx
            my = y0 + ty * sy
            color = c["accent"] if task.id == self.studio.selected else (
                c["success"] if self._status.get(task.id) == "succeeded"
                else c["muted"])
            self.create_rectangle(mx, my, mx + max(3, CARD_W * sx),
                                  my + max(2, CARD_H * sy), fill=color,
                                  outline="", tags="minimap")

    # -- interaction -----------------------------------------------------------

    def _task_at(self, event) -> str | None:
        tags = self.gettags(f"@{event.x},{event.y}")
        for t in tags:
            if t.startswith("card:"):
                return t[5:]
            if t.startswith("port-out:"):
                return None
        return None

    def _port_out_at(self, event) -> str | None:
        for t in self.gettags(f"@{event.x},{event.y}"):
            if t.startswith("port-out:"):
                return t[9:]
        return None

    def _on_click(self, event) -> None:
        out_port = self._port_out_at(event)
        if out_port:
            self._link_drag = {"from": out_port, "x": event.x, "y": event.y}
            return
        task_id = self._task_at(event)
        self.studio.selected = task_id
        if task_id:
            self._drag = {"id": task_id, "x": event.x, "y": event.y,
                          "moved": False}
            self.focus_set()
            self.render()
            self.studio.inspector.refresh()

    def _on_double_click(self, event) -> None:
        task_id = self._task_at(event)
        if task_id:
            self._rename_inline(task_id)

    def _rename_inline(self, task_id: str) -> None:
        task = next((t for t in self.studio.plan.tasks
                     if t.id == task_id), None)
        if task is None:
            return
        x, y = self._positions[task.id]
        entry = tk.Entry(self, background=self.theme.c["surface"],
                         foreground=self.theme.c["text"],
                         insertbackground=self.theme.c["text"],
                         font=self.theme.font(0, "bold"))
        self.create_window(self._scaled(x) + self._scaled(CARD_W) / 2,
                           self._scaled(y) + 14, window=entry,
                           width=int(self._scaled(CARD_W) - 16))
        entry.insert(0, task.title)
        entry.focus_set()
        entry.select_range(0, "end")

        def commit(_e=None):
            value = entry.get().strip()[:80]
            entry.destroy()
            if value and value != task.title:
                def apply(plan):
                    t = next(t for t in plan.tasks if t.id == task_id)
                    t.title = value
                self.studio.mutate(f"Renamed {task_id}", apply)

        entry.bind("<Return>", commit)
        entry.bind("<Escape>", lambda e: entry.destroy())
        entry.bind("<FocusOut>", commit)

    def _on_drag(self, event) -> None:
        if self._link_drag is not None:
            self.delete("linkline")
            self.create_line(self._link_drag["x"], self._link_drag["y"],
                             event.x, event.y, fill=self.theme.c["info"],
                             dash=(4, 2), tags="linkline")
            return
        if self._drag is None:
            return
        if abs(event.x - self._drag["x"]) + abs(
                event.y - self._drag["y"]) > 6:
            self._drag["moved"] = True
        self.delete("dropmarker")
        target = self._row_index_at(event.y)
        if target is not None:
            plan = self.studio.plan
            y = 24 + target * (CARD_H + GAP_Y) - GAP_Y / 2
            self.create_line(10, self._scaled(y),
                             24 + PER_ROW * (CARD_W + GAP_X) - 20,
                             self._scaled(y), fill=self.theme.c["accent"],
                             width=2, dash=(6, 3), tags="dropmarker")

    def _row_index_at(self, y: float) -> int | None:
        row_height = self._scaled(CARD_H + GAP_Y)
        if row_height <= 0:
            return None
        row = int((y - self._scaled(24) + row_height / 2) // row_height)
        if row < 0:
            return 0
        max_row = (len(self.studio.plan.tasks) + PER_ROW - 1) // PER_ROW \
            if self.studio.plan else 0
        return min(row, max_row)

    def _on_release(self, event) -> None:
        if self._link_drag is not None:
            task_id = self._task_at(event)
            src = self._link_drag["from"]
            self._link_drag = None
            self.delete("linkline")
            if task_id and task_id != src:
                if not self.studio.add_dependency(src, task_id):
                    pass                      # refused: reason shown in the
                                              # status line (spec tooltip)
            return
        if self._drag is not None and self._drag.get("moved"):
            src_id = self._drag["id"]
            self.delete("dropmarker")
            target_row = self._row_index_at(event.y)
            col = int((event.x - self._scaled(24)) //
                      self._scaled(CARD_W + GAP_X))
            col = max(0, min(PER_ROW - 1, col))
            target = target_row * PER_ROW + col if target_row is not None \
                else None
            self._drag = None
            if target is not None:
                self._reorder_to(src_id, target)
            return
        self._drag = None
        self.delete("dropmarker")

    def _reorder_to(self, task_id: str, target_index: int) -> None:
        plan = self.studio.plan
        ids = [t.id for t in plan.tasks]
        if task_id not in ids:
            return
        ids.remove(task_id)
        target_index = max(0, min(len(ids), target_index))
        ids.insert(target_index, task_id)
        pos = {tid: k for k, tid in enumerate(ids)}
        for t in plan.tasks:
            for dep in t.needs:
                if pos[dep] > pos[t.id]:
                    self.app_refuse(f"{t.id} needs {dep}, "
                                    "so it cannot run before it.")
                    return

        def apply(plan_):
            order = {tid: k for k, tid in enumerate(ids)}
            plan_.tasks.sort(key=lambda t: order[t.id])
            self.studio._relink_gates(plan_)

        self.studio.mutate(f"Moved {task_id}", apply)

    def app_refuse(self, text: str) -> None:
        self.studio.app.live.configure(text=text)

    def _on_right_click(self, event) -> None:
        task_id = self._task_at(event)
        if not task_id:
            return
        self.studio.selected = task_id
        self.render()
        menu = tk.Menu(self, tearoff=0,
                       background=self.theme.c["surface"],
                       foreground=self.theme.c["text"],
                       activebackground=self.theme.c["accent"],
                       activeforeground=self.theme.c["accent_text"])
        menu.add_command(label="Rename", command=lambda:
                         self._rename_inline(task_id))
        menu.add_command(label="Open inspector", command=lambda:
                         self.studio.inspector.focus_tab(task_id))
        menu.add_command(label="Link to a later task…", command=lambda:
                         self._link_picker(None, task_id))
        menu.add_separator()
        menu.add_command(label="Delete", command=lambda:
                         self.studio.delete_node(task_id))
        menu.tk_popup(event.x_root, event.y_root)

    def _on_arrow(self, event) -> None:
        plan = self.studio.plan
        if not plan or not plan.tasks:
            return
        ids = [t.id for t in plan.tasks]
        cur = self.studio.selected or ids[0]
        i = ids.index(cur) if cur in ids else 0
        if event.keysym in ("Right", "Down"):
            i = min(len(ids) - 1, i + 1)
        else:
            i = max(0, i - 1)
        self.studio.selected = ids[i]
        self.render()
        self.studio.inspector.refresh()
        return "break"

    def _move_selected(self, delta: int) -> None:
        if self.studio.selected:
            self.studio.move_task(self.studio.selected, delta)
        return "break"

    def _delete_selected(self) -> None:
        if self.studio.selected:
            self.studio.delete_node(self.studio.selected)

    def _link_picker(self, _event=None, source: str | None = None) -> None:
        src = source or self.studio.selected
        plan = self.studio.plan
        if not src or plan is None:
            return
        ids = [t.id for t in plan.tasks]
        later = ids[ids.index(src) + 1:] if src in ids else []
        if not later:
            self.app_refuse(f"{src} is last; links point forward only.")
            return
        dlg = Dialog(self, f"Link {src} to…",
                     buttons=[("Link", "Primary.TButton", "link"),
                              ("Cancel", "", "close")])
        var = tk.StringVar(value=later[0])
        for i, tid in enumerate(later):
            task = next(t for t in plan.tasks if t.id == tid)
            ttk.Radiobutton(dlg.body, text=f"{tid} - {task.title}",
                            variable=var, value=tid).pack(
                anchor="w", padx=8, pady=2)
        dlg.on_link = lambda: self.studio.add_dependency(src, var.get())
        dlg.wait_window()

    # -- run status ------------------------------------------------------------

    def update_run_status(self, state) -> None:
        """Card status icons and handoff labels during a run (S6 drives)."""
        self._status = {tid: ts.state for tid, ts in state.tasks.items()}
        for tid, ts in state.tasks.items():
            if ts.handoff and ts.handoff.get("summary"):
                self._handoffs[tid] = str(ts.handoff["summary"])[:24]
        self.render()


class ListView(ttk.Frame):
    """The ordered, accessible plan table (spec S4 / S9)."""

    def __init__(self, parent, studio):
        super().__init__(parent)
        self.studio = studio
        cols = ("order", "title", "type", "needs", "model", "badge",
                "status")
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="↑ Move up",
                   command=lambda: self._move(-1)).pack(side="left")
        ttk.Button(bar, text="↓ Move down",
                   command=lambda: self._move(1)).pack(side="left", padx=4)
        ttk.Label(bar, text="Drag rows or use Alt+↑ / Alt+↓",
                  style="Muted.TLabel").pack(side="left", padx=8)
        self.tree = ttk.Treeview(self, columns=cols, show="headings",
                                 selectmode="browse")
        for cid, head, w in [("order", "#", 36), ("title", "Title", 260),
                             ("type", "Type", 90), ("needs", "Needs", 110),
                             ("model", "Model", 120), ("badge", "Risk", 90),
                             ("status", "Status", 110)]:
            self.tree.heading(cid, text=head)
            self.tree.column(cid, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Delete>", lambda e: self._delete())
        self.tree.bind("<Alt-Up>", lambda e: self._move(-1))
        self.tree.bind("<Alt-Down>", lambda e: self._move(1))
        self.tree.bind("<Return>", lambda e: self.studio.inspector.focus_tab())
        self.tree.bind("<Double-Button-1>", lambda e: self.studio.inspector.focus_tab())
        # Row drag: press on a row, release on another swaps them.
        self.tree.bind("<ButtonPress-1>", self._press)
        self.tree.bind("<ButtonRelease-1>", self._release)

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        plan = self.studio.plan
        if plan is None:
            return
        flags = self.studio.flags_map()
        statuses = self.studio.canvas_view._status if hasattr(
            self.studio, "canvas_view") else {}
        for i, task in enumerate(plan.tasks):
            f = flags.get(task.id, (False, False, False))
            status = statuses.get(task.id, "")
            name = accessible_name(self.studio, task, i, len(plan.tasks),
                                   status)
            self.tree.insert("", "end", iid=task.id, values=(
                i + 1, name, task.type, ", ".join(task.needs) or "—",
                (task.model or {}).get("model_id", "—"),
                f"{sum(f)} of 3"
                + (" needs gate" if all(f) and not task.gate_before else ""),
                status.replace("_", " ") or "—"))
        if self.studio.selected and self.tree.exists(self.studio.selected):
            self.tree.selection_set(self.studio.selected)

    def _selected_id(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _move(self, delta: int) -> None:
        tid = self._selected_id()
        if tid:
            self.studio.move_task(tid, delta)

    def _delete(self) -> None:
        tid = self._selected_id()
        if tid:
            self.studio.delete_node(tid)

    def _press(self, event) -> None:
        self._press_row = self.tree.identify_row(event.y)

    def _release(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if not row or row == getattr(self, "_press_row", None):
            return
        self.studio.add_dependency(self._press_row, row)
        self._press_row = None
