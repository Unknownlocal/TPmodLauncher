# SPDX-License-Identifier: MPL-2.0
"""Owned native settings tabs; edits cross the bridge as bounded data, never callbacks."""

import inspect
import re
import sys
import threading
import weakref


def settings_records(records):
    """Validate copied host declarations before native input or drawing sees them."""
    if type(records) is not list or len(records) > 4:
        raise ValueError("Expected at most four settings tabs")
    result, owners, labels = [], set(), set()
    for record in records:
        if type(record) is not dict or set(record) != {"owner", "label", "schema", "fields"}:
            raise ValueError("Invalid settings tab fields")
        owner = record["owner"]
        if type(owner) is not str or not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,127}", owner) or owner in owners:
            raise ValueError("Invalid settings owner")
        label = record["label"]
        if not _settings_label(label) or label in labels:
            raise ValueError("Invalid or duplicate settings tab label")
        if type(record["schema"]) is not int or not 1 <= record["schema"] <= 65535:
            raise ValueError("Invalid settings schema")
        if type(record["fields"]) is not list or not 1 <= len(record["fields"]) <= 64:
            raise ValueError("Expected 1-64 settings fields")
        fields, identifiers = [], set()
        for row in record["fields"]:
            if type(row) is not dict or set(row) != {"id", "label", "type", "default", "value"}:
                raise ValueError("Invalid settings field declaration")
            field_id = row["id"]
            if (type(field_id) is not str or not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,63}", field_id)
                    or field_id in identifiers or not _settings_label(row["label"]) or row["type"] != "color"):
                raise ValueError("Unsupported settings field")
            if not _settings_hex(row["default"]) or not _settings_hex(row["value"], len(row["default"])):
                raise ValueError("Invalid settings colour")
            identifiers.add(field_id)
            fields.append(dict(row))
        owners.add(owner)
        labels.add(label)
        result.append({**record, "fields": fields})
    return result


def _settings_label(value):
    return type(value) is str and 1 <= len(value) <= 64 and value == value.strip() and value.isprintable()


def _settings_hex(value, length=None):
    return (type(value) is str and re.fullmatch(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?", value) is not None
            and (length is None or len(value) == length))


class SettingsEditor:
    """One options screen owns its unsaved drafts and text focus across tab switches."""

    def __init__(self, adapter):
        self.adapter = adapter
        self.records, self.drafts, self.offsets = {}, {}, {}
        self.owner = self.focus = None
        self.cursor, self.select_all, self.text_input = 0, False, False
        self.sync()

    def sync(self):
        records = {row["owner"]: row for row in self.adapter.records}
        drafts = {}
        for owner, record in records.items():
            previous = {field["id"]: field["value"] for field in self.records.get(owner, {}).get("fields", [])}
            draft = self.drafts.get(owner, {})
            drafts[owner] = {field["id"]: (
                draft[field["id"]] if field["id"] in draft and draft[field["id"]] != previous.get(field["id"])
                else field["value"]
            ) for field in record["fields"]}
        self.records, self.drafts = records, drafts

    def blur(self, pg):
        self.focus = None
        if self.text_input:
            pg.key.stop_text_input()
            self.text_input = False

    def activate(self, owner, pg):
        if self.owner != owner:
            self.blur(pg)
            self.owner = owner

    def valid(self):
        return all(_settings_hex(self.drafts[self.owner][field["id"]], len(field["default"]))
                   for field in self.records[self.owner]["fields"])

    def save(self):
        if self.adapter.pending is None and self.valid():
            self.adapter.queue_save(self.owner, {
                "schema": self.records[self.owner]["schema"],
                "values": {key: value.upper() for key, value in self.drafts[self.owner].items()},
            })

    def reset(self):
        if self.adapter.pending is None:
            self.drafts[self.owner] = {row["id"]: row["default"] for row in self.records[self.owner]["fields"]}
            if self.focus is not None:
                field = self.records[self.owner]["fields"][self.focus]
                self.cursor, self.select_all = len(self.drafts[self.owner][field["id"]]), True
            self.adapter.messages[self.owner] = "Defaults restored. Save to keep them."

    def layout(self, screen, pg):
        native = screen._layout_rects()
        gap = max(6, screen.ui_gap(12))
        left, panel, footer = native["left"], native["panel"], native["footer"]
        body = pg.Rect(left.x, native["list_view"].y, max(1, panel.right - gap - left.x),
                       max(1, footer.top - gap - native["list_view"].y))
        button_h = max(screen.dp(36), screen.small.get_linesize() + gap)
        line_h = screen.small.get_linesize() + gap
        viewport = pg.Rect(body.x, body.y, body.w, max(1, body.h - button_h - 2 * line_h - gap))
        row_h = max(screen.dp(42), screen.small.get_linesize() + gap * 2)
        stride = row_h + max(4, gap // 2)
        maximum = max(0, stride * len(self.records[self.owner]["fields"]) - viewport.h)
        offset = max(0, min(self.offsets.get(self.owner, 0), maximum))
        self.offsets[self.owner] = offset
        button_w = min(max(screen.dp(125), screen.small.size("Reset to Defaults")[0] + gap * 2), body.w // 2)
        save = pg.Rect(body.right - button_w, body.bottom - button_h, button_w, button_h)
        reset = pg.Rect(save.x - gap - button_w, save.y, button_w, button_h)
        return body, viewport, row_h, stride, maximum, save, reset

    def focus_row(self, index, screen, pg):
        self.focus = index
        value = self.drafts[self.owner][self.records[self.owner]["fields"][index]["id"]]
        self.cursor, self.select_all = len(value), True
        if not self.text_input:
            pg.key.start_text_input()
            self.text_input = True
        _, viewport, row_h, stride, maximum, _, _ = self.layout(screen, pg)
        offset = self.offsets[self.owner]
        offset = min(offset, index * stride)
        offset = max(offset, index * stride + row_h - viewport.h)
        self.offsets[self.owner] = max(0, min(offset, maximum))

    def _insert(self, text):
        field = self.records[self.owner]["fields"][self.focus]
        original = value = self.drafts[self.owner][field["id"]]
        text = "".join(char for char in text if char.isprintable()).upper()
        if self.select_all:
            value, self.cursor = "", 0
        available = max(0, 9 - len(value))
        text = text[:available]
        self.drafts[self.owner][field["id"]] = value[:self.cursor] + text + value[self.cursor:]
        if self.drafts[self.owner][field["id"]] != original:
            self.adapter.messages.pop(self.owner, None)
        self.cursor += len(text)
        self.select_all = False

    def handle_event(self, screen, event, pg):
        body, viewport, row_h, stride, maximum, save, reset = self.layout(screen, pg)
        pending = self.adapter.pending is not None
        if event.type == pg.MOUSEWHEEL:
            self.offsets[self.owner] = max(0, min(maximum, self.offsets[self.owner] - event.y * stride * 3))
            return True
        if event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
            pos = screen.window_to_canvas(event.pos)
            if not body.collidepoint(pos):
                self.blur(pg)
                return False
            if not pending:
                if save.collidepoint(pos):
                    self.save()
                elif reset.collidepoint(pos):
                    self.reset()
                elif viewport.collidepoint(pos):
                    index = int((pos[1] - viewport.y + self.offsets[self.owner]) // stride)
                    if index < len(self.records[self.owner]["fields"]):
                        self.focus_row(index, screen, pg)
            return True
        if event.type == pg.TEXTINPUT:
            if self.focus is not None and not pending:
                self._insert(event.text)
            return True
        if event.type != pg.KEYDOWN:
            return body.collidepoint(screen.get_mouse_canvas_pos())
        if event.key == pg.K_ESCAPE:
            if self.focus is None:
                return False
            self.blur(pg)
            return True
        if pending:
            return True
        mods = getattr(event, "mod", 0)
        if event.key == pg.K_TAB:
            count = len(self.records[self.owner]["fields"])
            delta = -1 if mods & pg.KMOD_SHIFT else 1
            self.focus_row(((self.focus if self.focus is not None else (-1 if delta == 1 else 0)) + delta) % count,
                           screen, pg)
        elif event.key in (pg.K_RETURN, pg.K_KP_ENTER):
            self.save()
        elif event.key == pg.K_r and mods & pg.KMOD_CTRL:
            self.reset()
        elif self.focus is not None:
            field = self.records[self.owner]["fields"][self.focus]
            value = self.drafts[self.owner][field["id"]]
            if event.key == pg.K_a and mods & pg.KMOD_CTRL:
                self.select_all = True
            elif event.key == pg.K_v and mods & pg.KMOD_CTRL:
                try:
                    if not pg.scrap.get_init():
                        pg.scrap.init()
                    data = pg.scrap.get(pg.SCRAP_TEXT) or b""
                    self._insert(data[:64].decode("utf-8-sig", errors="ignore").strip("\x00\r\n "))
                except (AttributeError, pg.error):
                    pass
            elif event.key in (pg.K_BACKSPACE, pg.K_DELETE):
                original = value
                if self.select_all:
                    value, self.cursor = "", 0
                elif event.key == pg.K_BACKSPACE and self.cursor:
                    value = value[:self.cursor - 1] + value[self.cursor:]
                    self.cursor -= 1
                elif event.key == pg.K_DELETE:
                    value = value[:self.cursor] + value[self.cursor + 1:]
                self.drafts[self.owner][field["id"]], self.select_all = value, False
                if value != original:
                    self.adapter.messages.pop(self.owner, None)
            elif event.key in (pg.K_LEFT, pg.K_RIGHT, pg.K_HOME, pg.K_END):
                if event.key == pg.K_HOME or (self.select_all and event.key == pg.K_LEFT):
                    self.cursor = 0
                elif event.key == pg.K_END or (self.select_all and event.key == pg.K_RIGHT):
                    self.cursor = len(value)
                else:
                    self.cursor = max(0, min(len(value), self.cursor + (-1 if event.key == pg.K_LEFT else 1)))
                self.select_all = False
        return True

    def draw(self, screen, surface, module):
        pg, theme = module.pygame, module.UITheme
        body, viewport, row_h, stride, maximum, save, reset = self.layout(screen, pg)
        chrome = screen.ui_chrome
        gap = max(6, screen.ui_gap(12))
        native = screen._layout_rects()
        panel, footer = native["panel"], native["footer"]
        clear = pg.Rect(panel.x + 3, body.y - gap - 2, panel.w - 6, panel.bottom - body.y + gap - 1)
        pg.draw.rect(surface, chrome[theme.PANEL], clear, border_radius=16)
        swatch_size = max(8, row_h - gap)
        input_w = min(max(screen.dp(140), screen.small.size("#FFFFFFFF")[0] + gap * 2), max(1, viewport.w // 2))
        previous_clip = surface.get_clip()
        surface.set_clip(previous_clip.clip(viewport))
        try:
            for index, field in enumerate(self.records[self.owner]["fields"]):
                row = pg.Rect(viewport.x, viewport.y + index * stride - int(self.offsets[self.owner]), viewport.w, row_h)
                if not row.colliderect(viewport):
                    continue
                value = self.drafts[self.owner][field["id"]]
                valid = _settings_hex(value, len(field["default"]))
                swatch = pg.Rect(row.right - gap - swatch_size, row.y + (row_h - swatch_size) // 2,
                                 swatch_size, swatch_size)
                entry = pg.Rect(swatch.x - gap - input_w, row.y + 3, input_w, row_h - 6)
                pg.draw.rect(surface, chrome[theme.PANEL_ALT], row, border_radius=4)
                label = screen.fit_text(field["label"], screen.small, max(1, entry.x - row.x - gap * 2))
                surface.blit(screen.small.render(label, True, theme.TEXT), (row.x + gap, row.centery - screen.small.get_height() // 2))
                border = theme.DANGER if not valid else theme.PRIMARY if self.focus == index else theme.BORDER
                pg.draw.rect(surface, chrome[theme.CARD], entry, border_radius=3)
                pg.draw.rect(surface, chrome[border], entry, width=2, border_radius=3)
                if self.focus == index and self.select_all:
                    highlight = pg.Rect(entry.x + 4, entry.y + 3, min(entry.w - 8, screen.small.size(value)[0] + 4), entry.h - 6)
                    pg.draw.rect(surface, chrome[theme.PRIMARY_DARK], highlight)
                text_y = entry.centery - screen.small.get_height() // 2
                surface.blit(screen.small.render(value, True, theme.TEXT), (entry.x + 6, text_y))
                if self.focus == index and not self.select_all:
                    x = entry.x + 6 + screen.small.size(value[:self.cursor])[0]
                    pg.draw.line(surface, theme.TEXT, (x, entry.y + 6), (x, entry.bottom - 6))
                # Alpha colours are composited over a checkerboard rather than shown opaque.
                tile = max(3, swatch_size // 4)
                for y in range(0, swatch.h, tile):
                    for x in range(0, swatch.w, tile):
                        color = (105, 105, 105) if (x // tile + y // tile) % 2 else (175, 175, 175)
                        pg.draw.rect(surface, color, pg.Rect(swatch.x + x, swatch.y + y,
                                                           min(tile, swatch.w - x), min(tile, swatch.h - y)))
                if valid:
                    color = tuple(int(value[start:start + 2], 16) for start in range(1, len(value), 2))
                    paint = pg.Surface(swatch.size, pg.SRCALPHA)
                    paint.fill(color)
                    surface.blit(paint, swatch)
                else:
                    pg.draw.line(surface, theme.DANGER, swatch.topleft, swatch.bottomright, 2)
                pg.draw.rect(surface, chrome[theme.BORDER], swatch, width=1)
            if maximum:
                track = pg.Rect(viewport.right - 4, viewport.y, 4, viewport.h)
                height = max(12, int(track.h * viewport.h / (maximum + viewport.h)))
                thumb = pg.Rect(track.x, track.y + int((track.h - height) * self.offsets[self.owner] / maximum), 4, height)
                pg.draw.rect(surface, chrome[theme.BORDER_HOVER], thumb)
        finally:
            surface.set_clip(previous_clip)
        pending = self.adapter.pending is not None
        for rect, label, enabled in ((save, "Saving..." if pending else "Save", not pending and self.valid()),
                                     (reset, "Reset to Defaults", not pending)):
            pg.draw.rect(surface, chrome[theme.NEUTRAL_BUTTON if enabled else theme.DISABLED], rect, border_radius=4)
            text = screen.small.render(label, True, theme.TEXT if enabled else theme.DISABLED_TEXT)
            surface.blit(text, text.get_rect(center=rect.center))
        back = pg.Rect(body.x, footer.y + max(0, (footer.h - save.h) // 2), save.w, save.h)
        screen.back_button_rect = back
        pg.draw.rect(surface, chrome[theme.NEUTRAL_BUTTON], back, border_radius=4)
        text = screen.small.render("Back", True, theme.TEXT)
        surface.blit(text, text.get_rect(center=back.center))
        message = self.adapter.messages.get(self.owner, "Edit hex colours, then Save.")
        if not self.valid():
            message = "Use #RRGGBB, or #RRGGBBAA for colours with transparency."
        text = screen.fit_text(message, screen.small, max(1, body.w))
        surface.blit(screen.small.render(text, True, theme.TEXT_MUTED), (body.x, viewport.bottom + gap // 2))
        hint = screen.fit_text("Tab: next colour   Ctrl+A: select all   Enter: Save   Ctrl+R: reset", screen.small, max(1, body.w))
        surface.blit(screen.small.render(hint, True, theme.TEXT_DIM), (body.x, save.top - screen.small.get_linesize() - gap // 2))


class SettingsAdapter:
    """Own appended options tabs, wrappers and one acknowledged native save request."""

    def __init__(self, themes=None):
        self.themes = themes
        self.records, self.installed = [], []
        self.owner = self.module = self.tabs = None
        self.originals, self.wrappers, self.messages = {}, {}, {}
        self.editors = weakref.WeakSet()
        self.state, self.detail, self.generation = "idle", "", 0
        self.pending = None
        self.sequence = 0

    def _main(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Settings changes require the game main thread")

    @staticmethod
    def tab_key(owner):
        return "code-mod:" + owner

    def update(self, records, result=None):
        self._main()
        records = settings_records(records)
        if result is not None:
            if (type(result) is not dict or set(result) != {"id", "owner", "success"}
                    or type(result["id"]) is not int or result["id"] <= 0 or type(result["success"]) is not bool
                    or type(result["owner"]) is not str):
                raise ValueError("Invalid settings save result")
            if self.pending is not None and (result["id"], result["owner"]) == (self.pending["id"], self.pending["owner"]):
                self.messages[result["owner"]] = "Settings saved." if result["success"] else "Could not save settings. Try again."
                self.pending = None
        fallback_tab = self.tabs[0][0] if self.tabs else "game"
        self._restore()
        self.records = records
        self.generation += 1
        owners = {row["owner"] for row in records}
        keys = {self.tab_key(owner) for owner in owners}
        for editor in self.editors:
            screen = editor.screen()
            if screen is not None and screen.active_tab.startswith("code-mod:") and screen.active_tab not in keys:
                screen.active_tab = fallback_tab
        self.messages = {owner: message for owner, message in self.messages.items() if owner in owners}
        if self.pending is not None and self.pending["owner"] not in owners:
            self.pending = None
        self.detail = ""
        return self.refresh()

    def refresh(self):
        """Refresh native extensions without replacing the settings registry generation."""
        self._main()
        self._restore()
        self.state = "pending" if self.records or (self.themes is not None and self.themes.has_choices()) else "idle"
        return self.poll()

    def queue_save(self, owner, values):
        self._main()
        record = next(row for row in self.records if row["owner"] == owner)
        expected = {field["id"]: field["default"] for field in record["fields"]}
        if (type(values) is not dict or set(values) != {"schema", "values"}
                or type(values["schema"]) is not int or values["schema"] != record["schema"]
                or type(values["values"]) is not dict or values["values"].keys() != expected.keys()
                or any(not _settings_hex(values["values"][key], len(default)) for key, default in expected.items())):
            raise ValueError("Invalid native settings save")
        if self.pending is None:
            self.sequence += 1
            self.pending = {"id": self.sequence, "owner": owner, "schema": values["schema"], "values": dict(values["values"])}

    def _restore(self):
        conflict = False
        if self.owner is not None:
            for name, original in self.originals.items():
                current = inspect.getattr_static(self.owner, name, None)
                if current is self.wrappers[name]:
                    setattr(self.owner, name, original)
                elif current is not original:
                    conflict = True
            for item in self.installed:
                if item in self.tabs:
                    self.tabs.remove(item)
        if self.module is not None:
            for editor in self.editors:
                editor.blur(self.module.pygame)
                screen = editor.screen()
                if screen is not None:
                    if self.themes is not None:
                        self.themes.prepare_screen(screen)
                    screen._layout_cache = screen._chrome_cache = None
        self.owner = self.module = self.tabs = None
        self.originals, self.wrappers = {}, {}
        self.installed.clear()
        if conflict:
            raise RuntimeError("Settings methods were changed by another owner")

    def poll(self, completed=None):
        self._main()
        if self.state == "idle" and self.themes is not None and self.themes.has_choices():
            self.state = "pending"
        if self.state != "pending":
            return self.status()
        module = sys.modules.get("ui.screens.options_screen")
        if module is None or (getattr(getattr(module, "__spec__", None), "_initializing", False) and completed is not module):
            return self.status()
        try:
            owner, tabs = module.OptionsScreen, module.OPTION_TABS
            if (not isinstance(owner, type) or type(tabs) is not list
                    or any(type(item) is not tuple or len(item) != 2 or any(type(value) is not str for value in item) for item in tabs)):
                raise ValueError("Unsupported native settings interface")
            if any(row["label"] in {label for _, label in tabs} or self.tab_key(row["owner"]) in {key for key, _ in tabs}
                   for row in self.records):
                raise ValueError("Mod settings tab conflicts with a native tab")
            originals = {name: inspect.getattr_static(owner, name) for name in ("draw", "handle_event")}
            for name, fn in originals.items():
                signature = "(self, surface)" if name == "draw" else "(self, event)"
                if not inspect.isfunction(fn) or str(inspect.signature(fn)) != signature or hasattr(fn, "__wrapped__"):
                    raise ValueError("Unsupported native settings methods")
            wrappers = {}
            keys = {self.tab_key(row["owner"]): row["owner"] for row in self.records}

            def editor_for(screen):
                if self.themes is not None:
                    self.themes.prepare_screen(screen)
                editor = getattr(screen, "_tpm_settings_editor", None)
                if editor is None or editor.adapter is not self:
                    editor = SettingsEditor(self)
                    editor.screen = weakref.ref(screen)
                    screen._tpm_settings_editor = editor
                    self.editors.add(editor)
                if getattr(screen, "_tpm_settings_generation", None) != self.generation:
                    screen._layout_cache = screen._chrome_cache = None
                    screen._tpm_settings_generation = self.generation
                    editor.sync()
                if screen.active_tab.startswith("code-mod:") and screen.active_tab not in keys:
                    screen.active_tab = tabs[0][0]
                selected = keys.get(screen.active_tab)
                if selected is not None:
                    editor.activate(selected, module.pygame)
                else:
                    editor.blur(module.pygame)
                return editor, selected

            def active():
                return self.wrappers is wrappers and threading.current_thread() is threading.main_thread()

            def draw(screen, surface):
                if not active():
                    return originals["draw"](screen, surface)
                editor, selected = editor_for(screen)
                result = originals["draw"](screen, surface)
                if selected is not None:
                    editor.draw(screen, surface, module)
                return result

            def handle_event(screen, event):
                if not active():
                    return originals["handle_event"](screen, event)
                editor, selected = editor_for(screen)
                if selected is not None and editor.handle_event(screen, event, module.pygame):
                    return None
                result = originals["handle_event"](screen, event)
                if selected is not None and self.tab_key(selected) != screen.active_tab:
                    editor.blur(module.pygame)
                return result

            wrappers.update(draw=draw, handle_event=handle_event)
            self.owner, self.module, self.tabs = owner, module, tabs
            self.originals, self.wrappers = originals, wrappers
            for name, wrapper in wrappers.items():
                setattr(owner, name, wrapper)
            for row in self.records:
                item = self.tab_key(row["owner"]), row["label"]
                tabs.append(item)
                self.installed.append(item)
            self.state = "ready"
        except Exception as error:
            self.state, self.detail = "failed", str(error)[:512]
            self._restore()
        return self.status()

    def status(self):
        return {"state": self.state, "detail": self.detail, "generation": self.generation,
                "save": None if self.pending is None else {**self.pending, "values": dict(self.pending["values"])}}

    def close(self):
        self._main()
        self.pending = None
        if self.themes is not None:
            for editor in self.editors:
                screen = editor.screen()
                if screen is not None:
                    self.themes.prepare_screen(screen)
        self.themes = None
        self.update([])
